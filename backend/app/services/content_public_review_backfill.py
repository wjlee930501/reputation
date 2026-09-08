"""Bounded independent re-review of explicitly allowlisted public content.

The default CLI mode is read-only and never calls a provider. Execute mode reviews
only public rows whose existing AI review currently blocks the public read gate.
It never rewrites candidate fields. It writes ``essence_check_summary.ai_review`` and
increments the shared CAS revision after a candidate-, brief-, profile-, source-, and
current-Essence-bound compare-and-swap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.essence import (
    PHOTO_SOURCE_TYPES,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services import cost_guard
from app.services.content_ai_review import (
    REVIEW_SCHEMA_VERSION,
    ContentAiReview,
    ContentAiReviewStatus,
    ContentAiReviewUnavailableReason,
    candidate_review_coverage,
    candidate_review_payload,
    candidate_sha256,
    content_review_input_payload,
    review_generated_content,
)
from app.services.content_brief import is_usable_content_brief
from app.services.content_publication import public_candidate_review_safe
from app.services.essence_readiness import (
    get_essence_readiness_sync,
    resolve_essence_readiness,
)
from app.services.evidence_noise import load_evidence_noise_hash_sync
from app.services.sync_async_bridge import SyncAsyncBridge
from app.utils.db_locks import acquire_hospital_advisory_lock_sync

OPERATION_TYPE: Final = "CONTENT_PUBLIC_REVIEW_BACKFILL"
REVIEW_POLICY: Final = f"public-review-backfill-v1:{REVIEW_SCHEMA_VERSION}"
REVIEW_CONTEXT_KEY: Final = "public_review_context"
MAX_BATCH: Final = 25
MAX_PROVIDER_ATTEMPTS: Final = 3
LEASE_TTL: Final = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class PublicReviewBackfillResult:
    allowlisted: int = 0
    missing: int = 0
    inactive: int = 0
    current: int = 0
    cleared: int = 0
    blocked: int = 0
    unavailable: int = 0
    costblocked: int = 0
    stale: int = 0

    @property
    def unresolved(self) -> int:
        return (
            self.missing
            + self.inactive
            + self.blocked
            + self.unavailable
            + self.costblocked
            + self.stale
        )

    def to_dict(self) -> dict[str, int]:
        payload = asdict(self)
        payload["unresolved"] = self.unresolved
        return payload

    def with_outcome(self, outcome: str) -> PublicReviewBackfillResult:
        if outcome not in _OUTCOMES:
            raise ValueError(f"unknown public review backfill outcome: {outcome}")
        payload = asdict(self)
        payload[outcome] += 1
        return PublicReviewBackfillResult(**payload)


_OUTCOMES: Final = frozenset(
    {
        "missing",
        "inactive",
        "current",
        "cleared",
        "blocked",
        "unavailable",
        "costblocked",
        "stale",
    }
)


@dataclass(frozen=True, slots=True)
class _ReviewExpectation:
    content_id: uuid.UUID
    hospital_id: uuid.UUID
    content_revision: int
    candidate_hash: str
    candidate: dict[str, Any]
    brief: dict[str, Any] | None
    philosophy_id: uuid.UUID
    source_snapshot_hash: str
    review_input_hash: str

    @property
    def operation_key(self) -> str:
        payload = {
            "content_id": str(self.content_id),
            "candidate_sha256": self.candidate_hash,
            "philosophy_id": str(self.philosophy_id),
            "source_snapshot_hash": self.source_snapshot_hash,
            "review_policy": REVIEW_POLICY,
            "review_input_sha256": self.review_input_hash,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"content-public-review:{digest}"


@dataclass(frozen=True, slots=True)
class _ClaimedReview:
    expectation: _ReviewExpectation
    hospital: Hospital
    philosophy: Any
    run_id: uuid.UUID
    lease_token: str
    next_attempt: int


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    outcome: str
    provider_attempted: bool


def _brief(item: ContentItem) -> dict[str, Any] | None:
    value = item.content_brief
    return deepcopy(value) if is_usable_content_brief(value) else None


def _candidate(item: ContentItem) -> dict[str, Any]:
    # Keep this list identical to the normal generation re-review path.
    return candidate_review_payload(
        {
            "title": item.title,
            "body": item.body,
            "meta_description": item.meta_description,
            "faq_question": item.faq_question,
            "faq_answer_summary": item.faq_answer_summary,
            "references_list": deepcopy(item.references_list),
        }
    )


def _review_input_hash(
    hospital: Hospital,
    philosophy: Any,
    candidate: dict[str, Any],
    brief: dict[str, Any] | None,
) -> str:
    """Hash the exact normalized provider-visible review projection."""

    payload = content_review_input_payload(
        hospital=hospital,
        philosophy=philosophy,
        content=candidate,
        content_brief=brief,
    )
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _current_typed_block(item: ContentItem, expectation: _ReviewExpectation) -> bool:
    summary = item.essence_check_summary
    review = summary.get("ai_review") if isinstance(summary, dict) else None
    context = review.get(REVIEW_CONTEXT_KEY) if isinstance(review, dict) else None
    return bool(
        isinstance(review, dict)
        and review.get("schema_version") == REVIEW_SCHEMA_VERSION
        and review.get("status") == ContentAiReviewStatus.REVISE.value
        and review.get("blocking") is True
        and review.get("candidate_sha256") == candidate_sha256(item)
        and review.get("coverage") == candidate_review_coverage(item)
        and context
        == {
            "philosophy_id": str(expectation.philosophy_id),
            "source_snapshot_hash": expectation.source_snapshot_hash,
            "review_policy": REVIEW_POLICY,
            "review_input_sha256": expectation.review_input_hash,
        }
    )


def _inspect(
    db: Session, content_id: uuid.UUID
) -> tuple[str, _ReviewExpectation | None, Hospital | None, Any | None]:
    row = db.execute(
        select(ContentItem, Hospital)
        .join(Hospital, Hospital.id == ContentItem.hospital_id)
        .where(ContentItem.id == content_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        return "missing", None, None, None
    item, hospital = row
    if (
        item.status != ContentStatus.PUBLISHED
        or hospital.status != HospitalStatus.ACTIVE
        or not hospital.site_live
    ):
        return "inactive", None, hospital, None

    readiness = get_essence_readiness_sync(db, hospital.id)
    philosophy = readiness.current
    if philosophy is None or not philosophy.source_snapshot_hash:
        return "inactive", None, hospital, None
    if public_candidate_review_safe(item):
        # The staged rollout deliberately preserves the existing safe baseline:
        # review-absent rows and a current PASS do not consume provider budget.
        return "current", None, hospital, philosophy
    candidate = _candidate(item)
    brief = _brief(item)
    expectation = _ReviewExpectation(
        content_id=item.id,
        hospital_id=item.hospital_id,
        content_revision=int(item.content_revision or 1),
        candidate_hash=candidate_sha256(candidate),
        candidate=candidate,
        brief=brief,
        philosophy_id=philosophy.id,
        source_snapshot_hash=str(philosophy.source_snapshot_hash),
        review_input_hash=_review_input_hash(hospital, philosophy, candidate, brief),
    )
    if _current_typed_block(item, expectation):
        # A concrete hard/uncertain result belongs to the separate bounded body
        # remediation lane. Re-reviewing unchanged text cannot clear it safely.
        return "blocked", None, hospital, philosophy
    return "stale", expectation, hospital, philosophy


def _request_payload(expectation: _ReviewExpectation) -> dict[str, Any]:
    return {
        "content_id": str(expectation.content_id),
        "content_revision": expectation.content_revision,
        "candidate_sha256": expectation.candidate_hash,
        "philosophy_id": str(expectation.philosophy_id),
        "source_snapshot_hash": expectation.source_snapshot_hash,
        "review_input_sha256": expectation.review_input_hash,
        "review_policy": REVIEW_POLICY,
        "max_provider_attempts": MAX_PROVIDER_ATTEMPTS,
    }


def _claim(db: Session, content_id: uuid.UUID) -> tuple[str, _ClaimedReview | None]:
    hospital_id = db.execute(
        select(ContentItem.hospital_id).where(ContentItem.id == content_id)
    ).scalar_one_or_none()
    if hospital_id is None:
        db.rollback()
        return "missing", None
    # This is the same transaction lock used by every source mutation. It also
    # serializes same-hospital run creation, avoiding select-then-insert races.
    acquire_hospital_advisory_lock_sync(db, hospital_id)
    db.expire_all()
    outcome, expectation, hospital, philosophy = _inspect(db, content_id)
    if expectation is None or hospital is None or philosophy is None:
        db.rollback()
        return outcome, None

    run = db.execute(
        select(OperationRun)
        .where(
            OperationRun.hospital_id == expectation.hospital_id,
            OperationRun.operation_type == OPERATION_TYPE,
            OperationRun.idempotency_key == expectation.operation_key,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if run is None:
        run = OperationRun(
            hospital_id=expectation.hospital_id,
            operation_type=OPERATION_TYPE,
            idempotency_key=expectation.operation_key,
            state=OperationRunState.REQUESTED,
            total_count=1,
            request_payload=_request_payload(expectation),
        )
        db.add(run)
        db.flush()
    elif run.state == OperationRunState.SUCCEEDED:
        db.rollback()
        # Inspection already established that the stored review is unresolved.
        # A terminal success for the same stable inputs must not silently erase it.
        return "stale", None
    elif (
        run.state == OperationRunState.RUNNING
        and run.lease_expires_at is not None
        and run.lease_expires_at > now
    ):
        db.rollback()
        return "stale", None
    elif int(run.attempt_count or 0) >= MAX_PROVIDER_ATTEMPTS:
        run.state = OperationRunState.FAILED
        run.success_count = 0
        run.failure_count = 1
        run.skipped_count = 0
        run.lease_owner = None
        run.lease_expires_at = None
        run.safe_error_code = run.safe_error_code or "REVIEW_ATTEMPTS_EXHAUSTED"
        run.safe_error_message = (
            run.safe_error_message or "독립 검수 공급자 시도 상한에 도달했습니다."
        )
        run.completed_at = now
        db.commit()
        return "unavailable", None
    lease_token = str(uuid.uuid4())
    run.state = OperationRunState.RUNNING
    run.started_at = run.started_at or now
    run.completed_at = None
    run.lease_owner = lease_token
    run.lease_expires_at = now + LEASE_TTL
    run.request_payload = _request_payload(expectation)
    run.safe_error_code = None
    run.safe_error_message = None
    run.result_summary = None
    run.success_count = 0
    run.failure_count = 0
    run.skipped_count = 0
    next_attempt = int(run.attempt_count or 0) + 1
    run_id = run.id
    db.commit()
    return (
        "stale",
        _ClaimedReview(
            expectation=expectation,
            hospital=hospital,
            philosophy=philosophy,
            run_id=run_id,
            lease_token=lease_token,
            next_attempt=next_attempt,
        ),
    )


def _run_matches(db: Session, claim: _ClaimedReview) -> OperationRun | None:
    run = db.execute(
        select(OperationRun)
        .where(OperationRun.id == claim.run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        run is None
        or run.state != OperationRunState.RUNNING
        or run.lease_owner != claim.lease_token
        or run.request_payload != _request_payload(claim.expectation)
    ):
        return None
    return run


def _release_run(
    db: Session,
    claim: _ClaimedReview,
    *,
    code: str,
    message: str,
    refund_attempt: bool = False,
) -> bool:
    run = _run_matches(db, claim)
    if run is None:
        db.rollback()
        return False
    if refund_attempt:
        if int(run.attempt_count or 0) != claim.next_attempt:
            db.rollback()
            return False
        run.attempt_count = max(0, int(run.attempt_count or 0) - 1)
    run.state = (
        OperationRunState.FAILED
        if int(run.attempt_count or 0) >= MAX_PROVIDER_ATTEMPTS
        else OperationRunState.REQUESTED
    )
    run.success_count = 0
    run.failure_count = 1 if run.state == OperationRunState.FAILED else 0
    run.skipped_count = 0
    run.lease_owner = None
    run.lease_expires_at = None
    run.safe_error_code = code
    run.safe_error_message = message[:500]
    run.completed_at = datetime.now(timezone.utc) if run.state == OperationRunState.FAILED else None
    db.commit()
    return True


def _precommit_provider_attempt(db: Session, claim: _ClaimedReview) -> bool:
    run = _run_matches(db, claim)
    if run is None or int(run.attempt_count or 0) != claim.next_attempt - 1:
        db.rollback()
        return False
    run.attempt_count = claim.next_attempt
    run.heartbeat_at = datetime.now(timezone.utc)
    run.safe_error_code = None
    run.safe_error_message = None
    db.commit()
    return True


def _mark_stale(db: Session, claim: _ClaimedReview, reason: str) -> None:
    run = _run_matches(db, claim)
    if run is None:
        db.rollback()
        return
    run.state = OperationRunState.CANCELLED
    run.success_count = 0
    run.failure_count = 0
    run.skipped_count = 1
    run.lease_owner = None
    run.lease_expires_at = None
    run.safe_error_code = "SOURCE_CHANGED"
    run.safe_error_message = reason[:500]
    run.completed_at = datetime.now(timezone.utc)
    db.commit()


def _review_binding_valid(review: ContentAiReview, expectation: _ReviewExpectation) -> bool:
    return bool(
        review.candidate_sha256 == expectation.candidate_hash
        and review.coverage == candidate_review_coverage(expectation.candidate)
    )


def _checkpoint_review(
    db: Session,
    claim: _ClaimedReview,
    review: ContentAiReview,
) -> str:
    # Acquire before every row lock so source create/update cannot introduce a
    # phantom required source between snapshot validation and this commit.
    acquire_hospital_advisory_lock_sync(db, claim.expectation.hospital_id)
    db.expire_all()
    run = _run_matches(db, claim)
    if run is None:
        db.rollback()
        return "stale"
    item = db.execute(
        select(ContentItem)
        .where(ContentItem.id == claim.expectation.content_id)
        .with_for_update(of=ContentItem)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        item is None
        or item.status != ContentStatus.PUBLISHED
        or int(item.content_revision or 1) != claim.expectation.content_revision
        or candidate_sha256(item) != claim.expectation.candidate_hash
    ):
        db.rollback()
        _mark_stale(db, claim, "콘텐츠 후보 또는 승인 브리프가 검수 중 변경되었습니다.")
        return "stale"

    hospital = db.execute(
        select(Hospital)
        .where(Hospital.id == claim.expectation.hospital_id)
        .with_for_update(of=Hospital)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    philosophy = db.execute(
        select(HospitalContentPhilosophy)
        .where(
            HospitalContentPhilosophy.hospital_id == claim.expectation.hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
        .with_for_update(of=HospitalContentPhilosophy)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    sources = list(
        db.execute(
            select(HospitalSourceAsset)
            .where(
                HospitalSourceAsset.hospital_id == claim.expectation.hospital_id,
                HospitalSourceAsset.status != SourceStatus.EXCLUDED,
                HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
            )
            .with_for_update(of=HospitalSourceAsset)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    readiness = resolve_essence_readiness(
        philosophy,
        sources,
        excluded_note_hash=load_evidence_noise_hash_sync(db, claim.expectation.hospital_id),
    )
    current = readiness.current
    if (
        hospital is None
        or hospital.status != HospitalStatus.ACTIVE
        or not hospital.site_live
        or current is None
        or current.id != claim.expectation.philosophy_id
        or str(current.source_snapshot_hash or "") != claim.expectation.source_snapshot_hash
        or _review_input_hash(hospital, current, _candidate(item), _brief(item))
        != claim.expectation.review_input_hash
    ):
        db.rollback()
        _mark_stale(db, claim, "자료 snapshot 또는 현재 승인 Essence가 검수 중 변경되었습니다.")
        return "stale"

    summary = (
        dict(item.essence_check_summary)
        if isinstance(item.essence_check_summary, dict)
        else {}
    )
    review_payload = review.payload()
    review_payload[REVIEW_CONTEXT_KEY] = {
        "philosophy_id": str(claim.expectation.philosophy_id),
        "source_snapshot_hash": claim.expectation.source_snapshot_hash,
        "review_policy": REVIEW_POLICY,
        "review_input_sha256": claim.expectation.review_input_hash,
    }
    summary["ai_review"] = review_payload
    item.essence_check_summary = summary
    # This metadata participates in public safety. Bump the shared content CAS
    # revision so a concurrent image/generation write cannot replace the summary
    # that was read before this independent review completed.
    item.content_revision = int(item.content_revision or 1) + 1

    outcome = "blocked" if review.blocking_findings else "cleared"
    run.state = OperationRunState.SUCCEEDED
    run.success_count = 1
    run.failure_count = 0
    run.skipped_count = 0
    run.lease_owner = None
    run.lease_expires_at = None
    run.result_summary = {
        "outcome": outcome,
        "content_id": str(item.id),
        "reviewed_revision": claim.expectation.content_revision,
        "written_revision": item.content_revision,
    }
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    return outcome


async def _reserve(
    reserve: Callable[..., Awaitable[cost_guard.CostGuardDecision]],
    claim: _ClaimedReview,
) -> cost_guard.CostGuardDecision:
    return await reserve(
        category="content",
        count=1,
        reservation_id=(
            f"content-public-review:{claim.run_id}:lease:{claim.lease_token}:"
            f"attempt:{claim.next_attempt}"
        ),
    )


def _process_one(
    db: Session,
    content_id: uuid.UUID,
    *,
    reviewer: Callable[..., Awaitable[ContentAiReview]],
    reserve: Callable[..., Awaitable[cost_guard.CostGuardDecision]],
    bridge: SyncAsyncBridge,
) -> _ProcessResult:
    outcome, claim = _claim(db, content_id)
    if claim is None:
        return _ProcessResult(outcome=outcome, provider_attempted=False)

    try:
        decision = bridge.run(_reserve(reserve, claim))
    except Exception as exc:
        _release_run(
            db,
            claim,
            code="COST_GUARD_UNAVAILABLE",
            message=type(exc).__name__,
        )
        return _ProcessResult(outcome="unavailable", provider_attempted=False)
    logical_call_id = f"content-public-review:{claim.run_id}"
    attempt_id = f"{logical_call_id}:attempt:{claim.next_attempt}"
    if not decision.allowed:
        # Call the typed helper with the denied decision so the result carries a
        # machine-readable COST_BLOCKED diagnostic without a second reservation.
        try:
            review = bridge.run(
                reviewer(
                    hospital=claim.hospital,
                    philosophy=claim.philosophy,
                    content=claim.expectation.candidate,
                    content_brief=claim.expectation.brief,
                    cost_decision=decision,
                    logical_call_id=logical_call_id,
                    attempt_id=attempt_id,
                    http_attempt=claim.next_attempt,
                )
            )
        except Exception as exc:
            _release_run(
                db,
                claim,
                code="COST_BLOCKED",
                message=type(exc).__name__,
            )
            return _ProcessResult(outcome="costblocked", provider_attempted=False)
        _release_run(
            db,
            claim,
            code="COST_BLOCKED",
            message=review.summary,
        )
        return _ProcessResult(outcome="costblocked", provider_attempted=False)

    if not _precommit_provider_attempt(db, claim):
        bridge.run(cost_guard.settle_reservation(decision.receipt, consumed_units=0))
        return _ProcessResult(outcome="stale", provider_attempted=False)

    try:
        review = bridge.run(
            reviewer(
                hospital=claim.hospital,
                philosophy=claim.philosophy,
                content=claim.expectation.candidate,
                content_brief=claim.expectation.brief,
                cost_decision=decision,
                logical_call_id=logical_call_id,
                attempt_id=attempt_id,
                http_attempt=claim.next_attempt,
            )
        )
    except Exception as exc:
        # The durable attempt was committed before entering the review helper.
        # Unknown failures are conservatively charged as an attempted provider call.
        _release_run(
            db,
            claim,
            code="REVIEW_UNAVAILABLE",
            message=type(exc).__name__,
        )
        return _ProcessResult(outcome="unavailable", provider_attempted=True)
    if review.provider_attempted is False:
        reason = review.unavailable_reason
        code = reason.value if reason is not None else "PROVIDER_NOT_ATTEMPTED"
        _release_run(
            db,
            claim,
            code=code,
            message=review.summary,
            refund_attempt=True,
        )
        return _ProcessResult(
            outcome=(
                "costblocked"
                if reason == ContentAiReviewUnavailableReason.COST_BLOCKED
                else "unavailable"
            ),
            provider_attempted=False,
        )
    if review.status == ContentAiReviewStatus.UNAVAILABLE:
        code = (
            review.unavailable_reason.value
            if review.unavailable_reason is not None
            else "REVIEW_UNAVAILABLE"
        )
        _release_run(db, claim, code=code, message=review.summary)
        return _ProcessResult(outcome="unavailable", provider_attempted=True)
    if not _review_binding_valid(review, claim.expectation):
        _release_run(
            db,
            claim,
            code="INVALID_REVIEW_BINDING",
            message="독립 검수 결과가 현재 전체 후보와 일치하지 않습니다.",
        )
        return _ProcessResult(outcome="unavailable", provider_attempted=True)
    return _ProcessResult(
        outcome=_checkpoint_review(db, claim, review),
        provider_attempted=True,
    )


def run_content_public_review_backfill(
    db: Session,
    *,
    content_ids: set[uuid.UUID],
    limit: int = MAX_BATCH,
    dry_run: bool = True,
    reviewer: Callable[..., Awaitable[ContentAiReview]] = review_generated_content,
    reserve: Callable[..., Awaitable[cost_guard.CostGuardDecision]] = cost_guard.reserve,
) -> PublicReviewBackfillResult:
    """Inspect an exact allowlist and optionally review a bounded subset."""

    ordered_ids = sorted(content_ids, key=str)
    result = PublicReviewBackfillResult(allowlisted=len(ordered_ids))
    provider_slots = max(0, min(int(limit), MAX_BATCH))
    attempted = 0
    with SyncAsyncBridge() as bridge:
        for content_id in ordered_ids:
            db.expire_all()
            outcome, expectation, _hospital, _philosophy = _inspect(db, content_id)
            db.rollback()
            if (
                dry_run
                or outcome != "stale"
                or expectation is None
                or attempted >= provider_slots
            ):
                result = result.with_outcome(outcome)
                continue
            attempt_result = _process_one(
                db,
                content_id,
                reviewer=reviewer,
                reserve=reserve,
                bridge=bridge,
            )
            if attempt_result.provider_attempted:
                attempted += 1
            result = result.with_outcome(attempt_result.outcome)
    if sum(getattr(result, outcome) for outcome in _OUTCOMES) != result.allowlisted:
        raise RuntimeError("public review result categories must be mutually exclusive")
    return result


def _manifest_content_ids(path: Path) -> set[uuid.UUID]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values: list[object] = []
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and isinstance(entry.get("contents"), list):
                values.extend(
                    content.get("id")
                    for content in entry["contents"]
                    if isinstance(content, dict)
                )
            elif isinstance(entry, dict):
                values.append(entry.get("id"))
            else:
                values.append(entry)
    elif isinstance(payload, dict):
        values.extend(payload.get("content_ids") or [])
    return {uuid.UUID(str(value)) for value in values if value}


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform bounded provider reviews")
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    parser.add_argument("--manifest-json", type=Path)
    parser.add_argument("--content-ids", help="comma-separated public content UUID allowlist")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()

    content_ids: set[uuid.UUID] = set()
    if args.manifest_json:
        content_ids.update(_manifest_content_ids(args.manifest_json))
    if args.content_ids:
        content_ids.update(
            uuid.UUID(value.strip())
            for value in args.content_ids.split(",")
            if value.strip()
        )
    if not content_ids:
        parser.error("an explicit --manifest-json or --content-ids allowlist is required")

    with SyncSessionLocal() as db:
        result = run_content_public_review_backfill(
            db,
            content_ids=content_ids,
            limit=args.limit,
            dry_run=not args.execute,
        )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 2 if args.require_complete and result.unresolved else 0


def main() -> None:
    raise SystemExit(_main())


if __name__ == "__main__":
    main()

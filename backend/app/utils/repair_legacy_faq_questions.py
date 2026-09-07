"""Repair three known public FAQ questions by appending one question mark.

This is a bounded release repair, not a general FAQ migration.  A default run is
read-only and can write a plan containing the current row revision and candidate
hashes.  ``--apply-plan`` accepts only that plan and compares the hospital,
content type, publication status, exact old question, revision, and complete
public-candidate hash before changing a row.

The repair deliberately does not create an AI-review certificate.  Since the FAQ
question participates in ``candidate_sha256``, every changed row is reported as
``DEFERRED`` until the existing independent-review pipeline reviews the new exact
candidate.  Existing review findings and every other content field are preserved.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.hospital import Hospital
from app.services.audit_log import write_audit_log_sync
from app.services.content_ai_review import (
    candidate_review_coverage,
    candidate_review_payload,
    candidate_sha256,
)

logger = logging.getLogger(__name__)

PLAN_SCHEMA: Final = "legacy_faq_question_punctuation_repair_v1"
EXPECTED_STATUS: Final = ContentStatus.PUBLISHED.value
REVIEW_STATE: Final = "DEFERRED"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AllowedRepair:
    content_id: str
    hospital_slug: str
    old_question: str

    @property
    def new_question(self) -> str:
        return f"{self.old_question}?"


# Explicitly copied from the 2026-09-07 public preflight snapshot.  Apply cannot
# be broadened with CLI ids or arbitrary replacement text.
ALLOWED_REPAIRS: Final[tuple[AllowedRepair, ...]] = (
    AllowedRepair(
        content_id="e4451df7-c392-4a56-b4c7-8a7fe32788b5",
        hospital_slug="haengbogdeurimyiweon",
        old_question="예방접종 초기 증상이 뭔지 알려줘",
    ),
    AllowedRepair(
        content_id="f11800fb-d2f4-4821-95ee-7774c702abb6",
        hospital_slug="noweontab365yiweon",
        old_question="상계동에서 도수치료 받을 수 있는 병원 추천해줘",
    ),
    AllowedRepair(
        content_id="359a77f7-c019-44c5-93e6-2c8bdd145fda",
        hospital_slug="noweontab365yiweon",
        old_question="노원구 응급의학과 병원 어디가 좋은지 비교해줘",
    ),
)


@dataclass(frozen=True)
class RepairPlanEntry:
    content_id: str
    hospital_id: str
    hospital_slug: str
    old_question: str
    new_question: str
    expected_revision: int
    expected_status: str
    candidate_sha256_before: str
    candidate_sha256_after: str
    ai_review_status_before: str | None
    ai_review_candidate_sha256_before: str | None
    ai_review_was_current: bool
    unresolved_review_preserved: bool
    review_disposition: str


@dataclass(frozen=True)
class RepairPlan:
    schema: str
    created_at: str
    entries: tuple[RepairPlanEntry, ...]
    require_complete_allowlist: bool = True
    review_state_after_apply: str = REVIEW_STATE
    public_ready_after_apply: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entries"] = [asdict(entry) for entry in self.entries]
        return payload


@dataclass(frozen=True)
class ApplyResult:
    repair_state: str
    updated_count: int
    review_state: str
    public_ready: bool
    content_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["content_ids"] = list(self.content_ids)
        return payload


class RepairSafetyError(RuntimeError):
    """Raised when the live row no longer equals the reviewed repair plan."""


def _allowed_by_id() -> dict[str, AllowedRepair]:
    return {target.content_id: target for target in ALLOWED_REPAIRS}


def _review_snapshot(item: ContentItem, before_hash: str) -> dict[str, Any]:
    summary = item.essence_check_summary
    review = summary.get("ai_review") if isinstance(summary, dict) else None
    if not isinstance(review, dict):
        return {
            "status": None,
            "candidate_sha256": None,
            "current": False,
            "unresolved": False,
            "disposition": "NO_CURRENT_REVIEW_REVIEW_DEFERRED",
        }

    status = str(review.get("status") or "") or None
    review_hash = str(review.get("candidate_sha256") or "") or None
    current = bool(
        review_hash == before_hash
        and isinstance(review.get("coverage"), dict)
        and review["coverage"] == candidate_review_coverage(item)
    )
    unresolved = status in {"REVISE", "UNAVAILABLE"}
    if unresolved:
        disposition = "EXISTING_UNRESOLVED_REVIEW_PRESERVED_REVIEW_DEFERRED"
    elif current:
        disposition = "CURRENT_REVIEW_WILL_BECOME_STALE_REVIEW_DEFERRED"
    else:
        disposition = "NO_CURRENT_REVIEW_REVIEW_DEFERRED"
    return {
        "status": status,
        "candidate_sha256": review_hash,
        "current": current,
        "unresolved": unresolved,
        "disposition": disposition,
    }


def _candidate_after_hash(item: ContentItem, new_question: str) -> str:
    payload = candidate_review_payload(item)
    payload["faq_question"] = new_question
    return candidate_sha256(payload)


def _load_rows(
    db: Session, *, lock: bool
) -> dict[str, tuple[ContentItem, Hospital]]:
    ids = [uuid.UUID(target.content_id) for target in ALLOWED_REPAIRS]
    statement = (
        select(ContentItem, Hospital)
        .join(Hospital, Hospital.id == ContentItem.hospital_id)
        .where(ContentItem.id.in_(ids))
    )
    if lock:
        statement = statement.with_for_update(of=ContentItem)
    return {
        str(item.id): (item, hospital)
        for item, hospital in db.execute(statement).all()
    }


def create_repair_plan(db: Session) -> RepairPlan:
    """Read and bind all three live rows without making any mutation."""

    rows = _load_rows(db, lock=False)
    entries: list[RepairPlanEntry] = []
    for target in ALLOWED_REPAIRS:
        pair = rows.get(target.content_id)
        if pair is None:
            raise RepairSafetyError(f"allowlisted content is missing: {target.content_id}")
        item, hospital = pair
        actual_type = str(getattr(item.content_type, "value", item.content_type))
        actual_status = str(getattr(item.status, "value", item.status))
        if hospital.slug != target.hospital_slug:
            raise RepairSafetyError(
                f"hospital slug mismatch for {target.content_id}: {hospital.slug}"
            )
        if actual_type != ContentType.FAQ.value:
            raise RepairSafetyError(
                f"content type mismatch for {target.content_id}: {actual_type}"
            )
        if actual_status != EXPECTED_STATUS:
            raise RepairSafetyError(
                f"status mismatch for {target.content_id}: {actual_status}"
            )
        if item.faq_question != target.old_question:
            raise RepairSafetyError(
                f"question mismatch for {target.content_id}: {item.faq_question!r}"
            )
        if not item.faq_answer_summary or not item.faq_answer_summary.strip():
            raise RepairSafetyError(f"FAQ answer is empty: {target.content_id}")

        before_hash = candidate_sha256(item)
        after_hash = _candidate_after_hash(item, target.new_question)
        review = _review_snapshot(item, before_hash)
        entries.append(
            RepairPlanEntry(
                content_id=target.content_id,
                hospital_id=str(hospital.id),
                hospital_slug=target.hospital_slug,
                old_question=target.old_question,
                new_question=target.new_question,
                expected_revision=int(item.content_revision),
                expected_status=EXPECTED_STATUS,
                candidate_sha256_before=before_hash,
                candidate_sha256_after=after_hash,
                ai_review_status_before=review["status"],
                ai_review_candidate_sha256_before=review["candidate_sha256"],
                ai_review_was_current=review["current"],
                unresolved_review_preserved=review["unresolved"],
                review_disposition=review["disposition"],
            )
        )

    return RepairPlan(
        schema=PLAN_SCHEMA,
        created_at=datetime.now(timezone.utc).isoformat(),
        entries=tuple(entries),
    )


def _validate_plan(plan: RepairPlan) -> None:
    if plan.schema != PLAN_SCHEMA:
        raise RepairSafetyError(f"unsupported plan schema: {plan.schema}")
    if not plan.require_complete_allowlist:
        raise RepairSafetyError("plan must require the complete explicit allowlist")
    if plan.review_state_after_apply != REVIEW_STATE or plan.public_ready_after_apply:
        raise RepairSafetyError("plan must keep independent review deferred")
    allowed = _allowed_by_id()
    if {entry.content_id for entry in plan.entries} != set(allowed):
        raise RepairSafetyError("plan must contain the complete explicit allowlist exactly once")
    if len(plan.entries) != len(allowed):
        raise RepairSafetyError("plan contains duplicate content ids")

    for entry in plan.entries:
        target = allowed[entry.content_id]
        if (
            entry.hospital_slug != target.hospital_slug
            or entry.old_question != target.old_question
            or entry.new_question != target.new_question
            or entry.expected_status != EXPECTED_STATUS
        ):
            raise RepairSafetyError(f"plan broadens allowlist: {entry.content_id}")
        try:
            uuid.UUID(entry.content_id)
            uuid.UUID(entry.hospital_id)
        except (TypeError, ValueError) as exc:
            raise RepairSafetyError(f"invalid UUID in plan: {entry.content_id}") from exc
        if entry.expected_revision < 1:
            raise RepairSafetyError(f"invalid revision for {entry.content_id}")
        if not _SHA256_RE.fullmatch(entry.candidate_sha256_before):
            raise RepairSafetyError(f"invalid before hash for {entry.content_id}")
        if not _SHA256_RE.fullmatch(entry.candidate_sha256_after):
            raise RepairSafetyError(f"invalid after hash for {entry.content_id}")


def _row_state(
    entry: RepairPlanEntry, item: ContentItem, hospital: Hospital
) -> str:
    actual_type = str(getattr(item.content_type, "value", item.content_type))
    actual_status = str(getattr(item.status, "value", item.status))
    base_matches = bool(
        str(item.id) == entry.content_id
        and str(item.hospital_id) == entry.hospital_id
        and str(hospital.id) == entry.hospital_id
        and hospital.slug == entry.hospital_slug
        and actual_type == ContentType.FAQ.value
        and actual_status == entry.expected_status
    )
    if not base_matches:
        return "MISMATCH"
    if (
        item.faq_question == entry.old_question
        and int(item.content_revision) == entry.expected_revision
        and candidate_sha256(item) == entry.candidate_sha256_before
        and _candidate_after_hash(item, entry.new_question) == entry.candidate_sha256_after
    ):
        return "ORIGINAL"
    if (
        item.faq_question == entry.new_question
        and int(item.content_revision) == entry.expected_revision + 1
        and candidate_sha256(item) == entry.candidate_sha256_after
    ):
        return "APPLIED"
    return "MISMATCH"


def _cas_update(db: Session, entry: RepairPlanEntry) -> int:
    statement = (
        update(ContentItem)
        .where(
            ContentItem.id == uuid.UUID(entry.content_id),
            ContentItem.hospital_id == uuid.UUID(entry.hospital_id),
            ContentItem.content_type == ContentType.FAQ,
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.faq_question == entry.old_question,
            ContentItem.content_revision == entry.expected_revision,
        )
        .values(
            faq_question=entry.new_question,
            content_revision=entry.expected_revision + 1,
        )
        # The rows were loaded under SELECT FOR UPDATE above. Keep that identity
        # map current so a verification read or idempotent replay in the same
        # process observes the committed punctuation repair.
        .execution_options(synchronize_session="fetch")
    )
    return int(db.execute(statement).rowcount or 0)


def apply_repair_plan(db: Session, plan: RepairPlan) -> ApplyResult:
    """Apply all three punctuation changes atomically, or change nothing."""

    _validate_plan(plan)
    entries = {entry.content_id: entry for entry in plan.entries}
    try:
        rows = _load_rows(db, lock=True)
        if set(rows) != set(entries):
            raise RepairSafetyError("one or more allowlisted rows are missing")
        states = {
            content_id: _row_state(entries[content_id], item, hospital)
            for content_id, (item, hospital) in rows.items()
        }
        if set(states.values()) == {"APPLIED"}:
            db.rollback()
            return ApplyResult(
                repair_state="ALREADY_APPLIED_REVIEW_DEFERRED",
                updated_count=0,
                review_state=REVIEW_STATE,
                public_ready=False,
                content_ids=tuple(target.content_id for target in ALLOWED_REPAIRS),
            )
        if set(states.values()) != {"ORIGINAL"}:
            mismatches = sorted(
                content_id for content_id, state in states.items() if state != "ORIGINAL"
            )
            raise RepairSafetyError(
                "plan no longer matches all rows; no changes applied: " + ", ".join(mismatches)
            )

        for target in ALLOWED_REPAIRS:
            entry = entries[target.content_id]
            if _cas_update(db, entry) != 1:
                raise RepairSafetyError(
                    f"compare-and-swap failed; no changes applied: {entry.content_id}"
                )
            write_audit_log_sync(
                db,
                action="legacy_faq_question_punctuation_repaired",
                hospital_id=uuid.UUID(entry.hospital_id),
                actor="SYSTEM_LEGACY_FAQ_REPAIR",
                target_type="content_item",
                target_id=entry.content_id,
                detail={
                    "old_question": entry.old_question,
                    "new_question": entry.new_question,
                    "previous_revision": entry.expected_revision,
                    "new_revision": entry.expected_revision + 1,
                    "candidate_sha256_before": entry.candidate_sha256_before,
                    "candidate_sha256_after": entry.candidate_sha256_after,
                    "independent_review_state": REVIEW_STATE,
                    "public_ready": False,
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ApplyResult(
        repair_state="APPLIED_REVIEW_DEFERRED",
        updated_count=len(ALLOWED_REPAIRS),
        review_state=REVIEW_STATE,
        public_ready=False,
        content_ids=tuple(target.content_id for target in ALLOWED_REPAIRS),
    )


def write_plan(path: Path, plan: RepairPlan) -> None:
    """Create a plan without overwriting an earlier reviewed plan."""

    with path.open("x", encoding="utf-8") as handle:
        json.dump(plan.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def load_plan(path: Path) -> RepairPlan:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise RepairSafetyError("invalid repair plan document")
    try:
        plan = RepairPlan(
            schema=str(raw["schema"]),
            created_at=str(raw["created_at"]),
            entries=tuple(RepairPlanEntry(**entry) for entry in raw["entries"]),
            require_complete_allowlist=bool(raw["require_complete_allowlist"]),
            review_state_after_apply=str(raw["review_state_after_apply"]),
            public_ready_after_apply=bool(raw["public_ready_after_apply"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairSafetyError("invalid repair plan fields") from exc
    _validate_plan(plan)
    return plan


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan-out",
        type=Path,
        help="Dry-run and create a revision-bound JSON plan without overwriting a file.",
    )
    mode.add_argument(
        "--apply-plan",
        type=Path,
        help="Apply a previously generated plan. This makes no provider calls.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help=(
            "Require all three allowlisted rows. This is always enforced on apply and is "
            "required when writing a dry-run plan."
        ),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        with SyncSessionLocal() as db:
            if args.apply_plan:
                result = apply_repair_plan(db, load_plan(args.apply_plan))
                logger.warning(
                    "%s: punctuation repaired=%d; independent review is DEFERRED; "
                    "public_ready=false",
                    result.repair_state,
                    result.updated_count,
                )
                print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
                return 0

            plan = create_repair_plan(db)
            db.rollback()
            if args.plan_out:
                if not args.require_complete:
                    raise RepairSafetyError(
                        "--plan-out requires --require-complete for release execution"
                    )
                write_plan(args.plan_out, plan)
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            logger.warning(
                "DRY_RUN: no rows changed and no provider called; apply remains review-deferred"
            )
            return 0
    except (OSError, json.JSONDecodeError, RepairSafetyError) as exc:
        logger.error("SAFE_ABORT: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())

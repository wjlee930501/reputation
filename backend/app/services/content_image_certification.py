"""Bounded migration of legacy public images to bound policy certificates."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.services import indexnow
from app.services.image_direction import hospital_image_direction
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    certify_existing_image_artifact,
    generate_image,
    image_content_hash_from_url,
    image_subject_hash,
    store_certified_image_bytes,
)
from app.services.image_policy import ImagePolicyRejectedError
from app.services.sync_async_bridge import SyncAsyncBridge

_STATE_KEY = "legacy_image_certification"
_LEASE_TTL = timedelta(hours=2)
_MAX_BATCH = 25
_MAX_REVIEW_ATTEMPTS = 3
_MAX_REPLACEMENT_ATTEMPTS = 2
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageCertificationBackfillResult:
    global_candidates: int = 0
    candidates: int = 0
    allowlisted: int = 0
    missing_db_ids: int = 0
    already_current: int = 0
    no_longer_public: int = 0
    no_image: int = 0
    inactive_site: int = 0
    remaining: int = 0
    current: int = 0
    certified: int = 0
    replaced: int = 0
    safe_pending_upload: int = 0
    replacement_required: int = 0
    review_exhausted: int = 0
    replacement_exhausted: int = 0
    cost_blocked: int = 0
    unavailable: int = 0
    cas_stale: int = 0
    leased: int = 0


def _bound_certificate_current(item: ContentItem) -> bool:
    url_hash = image_content_hash_from_url(item.image_url)
    return bool(
        item.image_policy_verified_at
        and item.image_content_hash
        and url_hash
        and url_hash == item.image_content_hash
        and item.image_subject_hash == image_subject_hash(item.content_type, item.title)
        and item.image_policy_version == IMAGE_POLICY_VERSION
    )


def _candidates(db, *, content_ids: set[uuid.UUID] | None = None) -> list[ContentItem]:
    predicates = [
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.image_url.isnot(None),
        Hospital.site_live.is_(True),
    ]
    if content_ids is not None:
        predicates.append(ContentItem.id.in_(content_ids))
    rows = list(
        db.execute(
            select(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(*predicates)
            .order_by(ContentItem.published_at, ContentItem.id)
            .options(joinedload(ContentItem.hospital))
        ).scalars()
    )
    return [item for item in rows if not _bound_certificate_current(item)]


@dataclass(frozen=True)
class _AllowlistClassification:
    candidates: tuple[ContentItem, ...]
    missing_db_ids: int
    already_current: int
    no_longer_public: int
    no_image: int
    inactive_site: int

    @property
    def remaining(self) -> int:
        return (
            len(self.candidates)
            + self.missing_db_ids
            + self.no_longer_public
            + self.no_image
            + self.inactive_site
        )


def _classify_allowlist(db, content_ids: set[uuid.UUID]) -> _AllowlistClassification:
    items = list(
        db.execute(
            select(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(ContentItem.id.in_(content_ids))
            .options(joinedload(ContentItem.hospital))
            .execution_options(populate_existing=True)
        ).scalars()
    )
    current = 0
    no_longer_public = 0
    no_image = 0
    inactive_site = 0
    candidates: list[ContentItem] = []
    for item in items:
        if item.status != ContentStatus.PUBLISHED:
            no_longer_public += 1
        elif not item.image_url:
            no_image += 1
        elif not item.hospital.site_live:
            inactive_site += 1
        elif _bound_certificate_current(item):
            current += 1
        else:
            candidates.append(item)
    return _AllowlistClassification(
        candidates=tuple(candidates),
        missing_db_ids=len(content_ids - {item.id for item in items}),
        already_current=current,
        no_longer_public=no_longer_public,
        no_image=no_image,
        inactive_site=inactive_site,
    )


@dataclass(frozen=True)
class _SourceExpectation:
    content_id: uuid.UUID
    revision: int
    image_url: str
    title: str | None
    content_type: object
    token: uuid.UUID


def _fingerprint(item: ContentItem) -> str:
    """Identify the paid review subject, independent of unrelated row revisions."""

    payload = "|".join(
        (
            str(item.id),
            str(item.content_type.value),
            str(item.title or ""),
            str(item.image_url or ""),
            IMAGE_POLICY_VERSION,
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _state(item: ContentItem) -> dict:
    summary = item.essence_check_summary
    value = summary.get(_STATE_KEY) if isinstance(summary, dict) else None
    if not isinstance(value, dict) or value.get("fingerprint") != _fingerprint(item):
        return {
            "fingerprint": _fingerprint(item),
            "source_revision": int(item.content_revision or 1),
            "review_attempts": 0,
            "replacement_attempts": 0,
            "status": "PENDING_REVIEW",
        }
    state = dict(value)
    state["source_revision"] = int(item.content_revision or 1)
    return state


def _expected_source(item: ContentItem, token: uuid.UUID) -> _SourceExpectation:
    return _SourceExpectation(
        content_id=item.id,
        revision=int(item.content_revision or 1),
        image_url=str(item.image_url),
        title=item.title,
        content_type=item.content_type,
        token=token,
    )


def _checkpoint_state(
    db,
    item: ContentItem,
    expected: _SourceExpectation,
    state: dict,
    *,
    release: bool,
) -> bool:
    """Merge attempt state only while this exact source/lease is authoritative."""

    fresh = _lock_expected_source(db, expected)
    if fresh is None:
        return False
    summary = dict(fresh.essence_check_summary) if isinstance(fresh.essence_check_summary, dict) else {}
    summary[_STATE_KEY] = state
    fresh.essence_check_summary = summary
    if release:
        fresh.generation_claim_token = None
        fresh.generation_claimed_at = None
    db.commit()
    db.refresh(item)
    return True


def _lock_expected_source(db, expected: _SourceExpectation) -> ContentItem | None:
    fresh = db.execute(
        select(ContentItem)
        .where(ContentItem.id == expected.content_id)
        .with_for_update(of=ContentItem)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        fresh is None
        or fresh.status != ContentStatus.PUBLISHED
        or fresh.generation_claim_token != expected.token
        or int(fresh.content_revision or 1) != expected.revision
        or fresh.image_url != expected.image_url
        or fresh.title != expected.title
        or fresh.content_type != expected.content_type
    ):
        db.rollback()
        _release(db, expected.content_id, expected.token)
        return None
    return fresh


def _claim(db, content_id: uuid.UUID) -> tuple[ContentItem, uuid.UUID] | None:
    item = db.execute(
        select(ContentItem)
        .where(ContentItem.id == content_id)
        .with_for_update(of=ContentItem)
        .options(joinedload(ContentItem.hospital))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    cutoff = datetime.now(timezone.utc) - _LEASE_TTL
    if (
        item is None
        or item.status != ContentStatus.PUBLISHED
        or not item.image_url
        or not (
            item.generation_claim_token is None
            or item.generation_claimed_at is None
            or item.generation_claimed_at < cutoff
        )
    ):
        db.rollback()
        return None
    token = uuid.uuid4()
    item.generation_claim_token = token
    item.generation_claimed_at = datetime.now(timezone.utc)
    db.commit()
    return item, token


def _release(db, content_id: uuid.UUID, token: uuid.UUID) -> None:
    db.execute(
        update(ContentItem)
        .where(ContentItem.id == content_id, ContentItem.generation_claim_token == token)
        .values(generation_claim_token=None, generation_claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _enqueue(db, item: ContentItem, revision: int) -> None:
    indexnow.enqueue_content_published_sync(
        db,
        slug=item.hospital.slug,
        content_id=item.id,
        aeo_domain=item.hospital.aeo_domain,
        treatments=item.hospital.treatments,
        revision=revision,
    )


def _review_and_copy(
    db,
    item: ContentItem,
    token: uuid.UUID,
    state: dict,
    bridge: SyncAsyncBridge,
) -> str:
    """Review once, checkpoint the byte hash, then retry upload without review."""

    image_url = str(item.image_url)
    revision = int(item.content_revision or 1)
    expected = _expected_source(item, token)
    artifact = None
    if state.get("status") != "SAFE_REVIEWED":
        if int(state.get("review_attempts") or 0) >= _MAX_REVIEW_ATTEMPTS:
            _release(db, item.id, token)
            return "review_exhausted"
        # Charge the durable logical-attempt budget before the provider call. A
        # crash after the HTTP response therefore cannot reset the budget.
        state["review_attempts"] = int(state.get("review_attempts") or 0) + 1
        state["observed_at"] = datetime.now(timezone.utc).isoformat()
        if not _checkpoint_state(db, item, expected, state, release=False):
            return "cas_stale"
        try:
            artifact = bridge.run(
                certify_existing_image_artifact(
                    image_url,
                    content_type=item.content_type,
                    topic=item.title,
                    hospital_id=item.hospital_id,
                )
            )
        except ImagePolicyRejectedError:
            state.update(
                {
                    "status": "REPLACEMENT_REQUIRED",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            state["fingerprint"] = _fingerprint(item)
            state["source_revision"] = revision + 1
            fresh = _lock_expected_source(db, expected)
            if fresh is None:
                return "cas_stale"
            summary = (
                dict(fresh.essence_check_summary)
                if isinstance(fresh.essence_check_summary, dict)
                else {}
            )
            summary[_STATE_KEY] = state
            fresh.image_policy_verified_at = None
            fresh.image_content_hash = None
            fresh.image_subject_hash = None
            fresh.image_policy_version = None
            fresh.essence_check_summary = summary
            fresh.content_revision = expected.revision + 1
            _enqueue(db, item, revision + 1)
            db.commit()
            return "replacement_required"
        except Exception as exc:
            is_cost = "cost capacity" in str(exc).casefold()
            if is_cost:
                state["review_attempts"] = max(
                    0, int(state.get("review_attempts") or 0) - 1
                )
            state.update(
                {
                    "status": (
                        "REVIEW_EXHAUSTED"
                        if int(state.get("review_attempts") or 0) >= _MAX_REVIEW_ATTEMPTS
                        else "PENDING_REVIEW"
                    ),
                    "last_error": "COST_BLOCKED" if is_cost else type(exc).__name__,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            if not _checkpoint_state(db, item, expected, state, release=True):
                return "cas_stale"
            if is_cost:
                return "cost_blocked"
            return "review_exhausted" if state["status"] == "REVIEW_EXHAUSTED" else "unavailable"
        state.update(
            {
                "status": "SAFE_REVIEWED",
                "content_hash": artifact.content_hash,
                "subject_hash": artifact.subject_hash,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if not _checkpoint_state(db, item, expected, state, release=False):
            return "cas_stale"
    else:
        from app.services.image_engine import _download_stored_image

        image_bytes = _download_stored_image(image_url)
        if hashlib.sha256(image_bytes).hexdigest() != state.get("content_hash"):
            state.update(status="PENDING_REVIEW", last_error="SOURCE_BYTES_CHANGED")
            _checkpoint_state(db, item, expected, state, release=True)
            return "cas_stale"
        artifact = type("StoredArtifact", (), {"image_bytes": image_bytes})()

    try:
        immutable_url = store_certified_image_bytes(artifact.image_bytes, item.hospital.slug)
    except Exception as exc:
        state["last_error"] = type(exc).__name__
        if not _checkpoint_state(db, item, expected, state, release=True):
            return "cas_stale"
        return "safe_pending_upload"
    fresh = _lock_expected_source(db, expected)
    if fresh is None:
        return "cas_stale"
    summary = (
        dict(fresh.essence_check_summary)
        if isinstance(fresh.essence_check_summary, dict)
        else {}
    )
    summary.pop(_STATE_KEY, None)
    fresh.image_url = immutable_url
    fresh.image_content_hash = state["content_hash"]
    fresh.image_subject_hash = state["subject_hash"]
    fresh.image_policy_version = IMAGE_POLICY_VERSION
    fresh.image_policy_verified_at = datetime.now(timezone.utc)
    fresh.essence_check_summary = summary
    fresh.content_revision = expected.revision + 1
    fresh.generation_claim_token = None
    fresh.generation_claimed_at = None
    _enqueue(db, item, revision + 1)
    db.commit()
    return "certified"


def _replace_unsafe(
    db,
    item: ContentItem,
    token: uuid.UUID,
    state: dict,
    bridge: SyncAsyncBridge,
) -> str:
    expected = _expected_source(item, token)
    attempts = int(state.get("replacement_attempts") or 0)
    if attempts >= _MAX_REPLACEMENT_ATTEMPTS:
        _release(db, item.id, token)
        return "replacement_exhausted"
    state["replacement_attempts"] = attempts + 1
    if not _checkpoint_state(db, item, expected, state, release=False):
        return "cas_stale"
    diagnostics: dict[str, str] = {}
    replacement_url, prompt = bridge.run(
        generate_image(
            item.content_type,
            item.hospital.slug,
            topic=item.title,
            direction=hospital_image_direction(item.hospital),
            hospital_id=item.hospital_id,
            diagnostics=diagnostics,
        )
    )
    if not replacement_url:
        if diagnostics.get("reason") == "COST_BLOCKED":
            state["replacement_attempts"] = attempts
            state["last_error"] = "COST_BLOCKED"
            if not _checkpoint_state(db, item, expected, state, release=True):
                return "cas_stale"
            return "cost_blocked"
        state["status"] = (
            "REPLACEMENT_EXHAUSTED"
            if state["replacement_attempts"] >= _MAX_REPLACEMENT_ATTEMPTS
            else "REPLACEMENT_REQUIRED"
        )
        if not _checkpoint_state(db, item, expected, state, release=True):
            return "cas_stale"
        return (
            "replacement_exhausted"
            if state["status"] == "REPLACEMENT_EXHAUSTED"
            else "replacement_required"
        )
    content_hash = image_content_hash_from_url(replacement_url)
    if content_hash is None:
        state["status"] = (
            "REPLACEMENT_EXHAUSTED"
            if state["replacement_attempts"] >= _MAX_REPLACEMENT_ATTEMPTS
            else "REPLACEMENT_REQUIRED"
        )
        state["last_error"] = "REPLACEMENT_HASH_MISSING"
        if not _checkpoint_state(db, item, expected, state, release=True):
            return "cas_stale"
        return (
            "replacement_exhausted"
            if state["status"] == "REPLACEMENT_EXHAUSTED"
            else "replacement_required"
        )
    fresh = _lock_expected_source(db, expected)
    if fresh is None:
        return "cas_stale"
    summary = (
        dict(fresh.essence_check_summary)
        if isinstance(fresh.essence_check_summary, dict)
        else {}
    )
    summary.pop(_STATE_KEY, None)
    fresh.image_url = replacement_url
    fresh.image_prompt = prompt
    fresh.image_content_hash = content_hash
    fresh.image_subject_hash = image_subject_hash(fresh.content_type, fresh.title)
    fresh.image_policy_version = IMAGE_POLICY_VERSION
    fresh.image_policy_verified_at = datetime.now(timezone.utc)
    fresh.essence_check_summary = summary
    fresh.content_revision = expected.revision + 1
    fresh.generation_claim_token = None
    fresh.generation_claimed_at = None
    _enqueue(db, item, expected.revision + 1)
    db.commit()
    return "replaced"


def _process_one(db, content_id: uuid.UUID, bridge: SyncAsyncBridge) -> str:
    claimed = _claim(db, content_id)
    if claimed is None:
        return "leased"
    item, token = claimed
    try:
        state = _state(item)
        if state["status"] == "REVIEW_EXHAUSTED":
            _release(db, content_id, token)
            return "review_exhausted"
        if state["status"] in {"REPLACEMENT_REQUIRED", "REPLACEMENT_EXHAUSTED"}:
            return _replace_unsafe(db, item, token, state, bridge)
        outcome = _review_and_copy(db, item, token, state, bridge)
        if outcome == "replacement_required":
            db.refresh(item)
            return _replace_unsafe(db, item, token, _state(item), bridge)
        return outcome
    except Exception:
        logger.exception("Legacy image certification failed for %s", content_id)
        db.rollback()
        _release(db, content_id, token)
        return "unavailable"


def run_legacy_image_certification_backfill(
    db,
    *,
    content_ids: set[uuid.UUID] | None = None,
    limit: int = _MAX_BATCH,
    dry_run: bool = True,
) -> ImageCertificationBackfillResult:
    """Inspect all unresolved rows or process at most 25 provider targets."""

    global_candidates = _candidates(db)
    classification = _classify_allowlist(db, content_ids) if content_ids else None
    candidates = (
        list(classification.candidates) if classification else list(global_candidates)
    )
    result = ImageCertificationBackfillResult(
        global_candidates=len(global_candidates),
        candidates=len(candidates),
        allowlisted=len(content_ids or ()),
        missing_db_ids=classification.missing_db_ids if classification else 0,
        already_current=classification.already_current if classification else 0,
        no_longer_public=classification.no_longer_public if classification else 0,
        no_image=classification.no_image if classification else 0,
        inactive_site=classification.inactive_site if classification else 0,
        remaining=classification.remaining if classification else len(candidates),
    )
    if dry_run:
        return result
    processed = 0
    with SyncAsyncBridge() as bridge:
        for candidate in candidates:
            if processed >= max(0, min(int(limit), _MAX_BATCH)):
                break
            outcome = _process_one(db, candidate.id, bridge)
            if outcome not in {"review_exhausted", "replacement_exhausted", "leased"}:
                processed += 1
            result = replace(result, **{outcome: getattr(result, outcome) + 1})
    fresh = _classify_allowlist(db, content_ids) if content_ids else None
    remaining = fresh.remaining if fresh else len(_candidates(db))
    return replace(
        result,
        current=(
            max(0, fresh.already_current - result.already_current)
            if fresh
            else max(0, result.candidates - remaining)
        ),
        remaining=remaining,
        missing_db_ids=fresh.missing_db_ids if fresh else 0,
        already_current=fresh.already_current if fresh else 0,
        no_longer_public=fresh.no_longer_public if fresh else 0,
        no_image=fresh.no_image if fresh else 0,
        inactive_site=fresh.inactive_site if fresh else 0,
    )


def _manifest_content_ids(path: str) -> set[uuid.UUID]:
    payload = json.loads(open(path, encoding="utf-8").read())
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform at most 25 reviews")
    parser.add_argument("--limit", type=int, default=_MAX_BATCH)
    parser.add_argument("--manifest-json")
    parser.add_argument("--content-ids", help="comma-separated public content UUID allowlist")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    content_ids: set[uuid.UUID] = set()
    if args.manifest_json:
        content_ids.update(_manifest_content_ids(args.manifest_json))
    if args.content_ids:
        content_ids.update(
            uuid.UUID(value.strip()) for value in args.content_ids.split(",") if value.strip()
        )
    if args.execute and not content_ids:
        parser.error("--execute requires --manifest-json or --content-ids")
    with SyncSessionLocal() as db:
        result = run_legacy_image_certification_backfill(
            db,
            content_ids=content_ids or None,
            limit=args.limit,
            dry_run=not args.execute,
        )
    print(json.dumps(asdict(result), sort_keys=True))
    if args.require_complete and (result.remaining or result.missing_db_ids):
        raise SystemExit(2)


if __name__ == "__main__":
    main()

"""Enqueue the operator-authorized rewrite for slots stuck on CONTENT_AI_HARD_FINDING.

Scheduled recovery deliberately treats a model-declared fact/medical-safety finding as
terminal: it claims the row, sees a stored body it is not allowed to rewrite, and finishes
without spending a writer session. That is the correct default — it is also why an outage
of this shape never clears by itself. This entry point is the human decision to buy one
HARD-aware writer session per slot: the stored findings become removal/hedge constraints,
the rewrite is independently re-reviewed, and publication is then decided by the unchanged
08:00 gate. A slot whose findings survive the rewrite stays blocked; nothing here can
publish content over an unresolved HARD finding.

Report-only by default (no database writes, no queue messages). ``--apply`` enqueues:

    python -m app.utils.force_hard_finding_rewrite <content_id> [<content_id> ...]
    python -m app.utils.force_hard_finding_rewrite --apply <content_id> [<content_id> ...]

Ids may also arrive in ``FORCE_HARD_REWRITE_CONTENT_IDS`` (comma or whitespace separated)
so the same image can run as a Cloud Run Job with ``SERVICE=force-hard-rewrite``.

Enqueue order is the publication order — oldest ``scheduled_date`` first, then
``sequence_no`` — so the longest-overdue slots reach the single content queue first.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.services.content_publication import assess_content_publication
from app.services.essence_readiness import get_current_approved_philosophy_sync

logger = logging.getLogger(__name__)

FORCED_BLOCK_CODE = "CONTENT_AI_HARD_FINDING"
CONTENT_IDS_ENV = "FORCE_HARD_REWRITE_CONTENT_IDS"
_TERMINAL_STATUSES = (ContentStatus.PUBLISHED, ContentStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class ForcedRewriteTarget:
    """One slot this run is willing to force, with the facts that ordered it."""

    content_id: uuid.UUID
    hospital_id: uuid.UUID
    scheduled_date: date | None
    sequence_no: int | None


@dataclass
class ForcedRewriteResult:
    targets: list[ForcedRewriteTarget] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    enqueued: list[uuid.UUID] = field(default_factory=list)


def _order_key(item: ContentItem) -> tuple[date, int, str]:
    # A slot with no scheduled date cannot claim the front of a publication queue.
    return (
        getattr(item, "scheduled_date", None) or date.max,
        int(getattr(item, "sequence_no", None) or 0),
        str(item.id),
    )


def plan_forced_hard_rewrites(
    items: Iterable[ContentItem],
    *,
    block_code_of: Callable[[ContentItem], str | None],
) -> ForcedRewriteResult:
    """Order the slots this run may force and name the reason for every refusal.

    Only the exact ops block code is forced. A slot that is publishable, already
    terminal, or blocked by a different cause is refused by name rather than pushed
    through a path that was not designed for its blocker.
    """

    result = ForcedRewriteResult()
    for item in sorted(items, key=_order_key):
        content_id = str(item.id)
        status = getattr(getattr(item, "status", None), "value", getattr(item, "status", None))
        if getattr(item, "status", None) in _TERMINAL_STATUSES:
            result.refused.append((content_id, f"status={status}"))
            continue
        code = block_code_of(item)
        if code is None:
            result.refused.append((content_id, "already publishable"))
            continue
        if code != FORCED_BLOCK_CODE:
            result.refused.append((content_id, f"blocked by {code}"))
            continue
        result.targets.append(
            ForcedRewriteTarget(
                content_id=item.id,
                hospital_id=item.hospital_id,
                scheduled_date=getattr(item, "scheduled_date", None),
                sequence_no=getattr(item, "sequence_no", None),
            )
        )
    return result


def _stored_block_code(db: Session, item: ContentItem) -> str | None:
    """Read the publication verdict the 08:00 gate would reach for this stored row."""

    philosophy = get_current_approved_philosophy_sync(db, item.hospital_id)
    assessment = assess_content_publication(item, philosophy)
    return None if assessment.publishable else assessment.code


def _enqueue(target: ForcedRewriteTarget) -> None:
    from app.workers.dispatch_auth import build_dispatch_headers
    from app.workers.tasks import regenerate_content_item

    content_id = str(target.content_id)
    regenerate_content_item.apply_async(
        args=[content_id, True],
        queue="content",
        headers=build_dispatch_headers("regenerate-content", content_id),
    )


def force_hard_finding_rewrites(
    content_ids: Sequence[uuid.UUID],
    *,
    apply_changes: bool = False,
    enqueue: Callable[[ForcedRewriteTarget], None] = _enqueue,
) -> ForcedRewriteResult:
    """Plan, report, and (with ``apply_changes``) enqueue one forced rewrite per slot."""

    with SyncSessionLocal() as db:
        items = list(
            db.execute(select(ContentItem).where(ContentItem.id.in_(list(content_ids))))
            .scalars()
            .all()
        )
        found = {item.id for item in items}
        result = plan_forced_hard_rewrites(
            items, block_code_of=lambda item: _stored_block_code(db, item)
        )
        # This entry point never writes. Release the read snapshot before any queue work.
        db.rollback()

    for missing in (content_id for content_id in content_ids if content_id not in found):
        result.refused.append((str(missing), "content item not found"))

    for target in result.targets:
        logger.info(
            "%s forced hard-finding rewrite: content=%s hospital=%s scheduled=%s sequence=%s",
            "enqueueing" if apply_changes else "would enqueue",
            target.content_id,
            target.hospital_id,
            target.scheduled_date,
            target.sequence_no,
        )
        if apply_changes:
            enqueue(target)
            result.enqueued.append(target.content_id)
    for content_id, reason in result.refused:
        logger.warning("refusing forced rewrite: content=%s reason=%s", content_id, reason)
    return result


def parse_content_ids(values: Sequence[str]) -> list[uuid.UUID]:
    """Accept ids from argv or one comma/whitespace separated environment value."""

    raw = list(values) or (os.environ.get(CONTENT_IDS_ENV) or "").replace(",", " ").split()
    parsed: list[uuid.UUID] = []
    for value in raw:
        cleaned = value.strip()
        if not cleaned:
            continue
        parsed.append(uuid.UUID(cleaned))
    # 같은 글을 두 번 넣으면 작가 세션을 두 번 산다. 순서는 유지하고 중복만 뺀다.
    return list(dict.fromkeys(parsed))


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enqueue an operator-authorized HARD-aware rewrite for content blocked by "
            f"{FORCED_BLOCK_CODE}. Publication remains owned by the existing gate."
        )
    )
    parser.add_argument("content_ids", nargs="*", help="Content item ids to force.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Enqueue the rewrites. Without this flag the run only reports its plan.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    content_ids = parse_content_ids(args.content_ids)
    if not content_ids:
        parser.error(f"no content ids given (argv or {CONTENT_IDS_ENV})")

    result = force_hard_finding_rewrites(content_ids, apply_changes=args.apply)
    logger.info(
        "forced hard-finding rewrite %s: requested=%d forced=%d enqueued=%d refused=%d",
        "applied" if args.apply else "dry run",
        len(content_ids),
        len(result.targets),
        len(result.enqueued),
        len(result.refused),
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())

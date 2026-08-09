"""Set-based query builder for the operations-center today queue."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.admin.operations_center_query_common import (
    OperationsFilters,
    SlaFilter,
    owner_predicate,
)
from app.api.admin.operations_center_serializers import owner_projection
from app.models.admin_user import AdminUser
from app.models.content import ContentItem, ContentStatus
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)

_OVERDUE_REVIEW_HOURS: Final = 24
_SEOUL: Final = ZoneInfo("Asia/Seoul")


async def load_today_queue(
    db: AsyncSession,
    filters: OperationsFilters,
    *,
    page: int,
    page_size: int,
    overview: bool,
    now: datetime,
) -> tuple[int, list[OperationsQueueRow]]:
    """Load due publishing and post-publication review work without row-level queries.

    The explicit pagination and overview inputs mirror the operations-center HTTP
    contract; their independent meanings make a compact parameter object misleading.
    """
    assignee = aliased(AdminUser)
    today = now.astimezone(_SEOUL).date()
    overdue_before = now - timedelta(hours=_OVERDUE_REVIEW_HOURS)
    waiting_review = and_(
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.post_publish_reviewed_at.is_(None),
        ContentItem.published_at.is_not(None),
    )
    due_publish = and_(
        ContentItem.scheduled_date <= today,
        ContentItem.status.in_((ContentStatus.DRAFT, ContentStatus.READY)),
    )
    task_state = case(
        (and_(waiting_review, ContentItem.published_at < overdue_before), "OVERDUE_REVIEW"),
        (waiting_review, "REVIEW_PENDING"),
        else_="PUBLISH_DUE",
    )
    severity = case(
        (and_(waiting_review, ContentItem.published_at < overdue_before), "HIGH"),
        else_="MEDIUM",
    )
    predicates = [or_(waiting_review, due_publish)]
    owner_filter = owner_predicate(assignee, filters.owner)
    if owner_filter is not None:
        predicates.append(owner_filter)
    if filters.status:
        predicates.append(task_state == filters.status)
    if filters.severity:
        predicates.append(severity == filters.severity)
    match filters.sla:
        case None:
            pass
        case SlaFilter.OVERDUE:
            predicates.append(and_(waiting_review, ContentItem.published_at < overdue_before))
        case SlaFilter.DUE:
            predicates.append(or_(ContentItem.published_at >= overdue_before, due_publish))
        case SlaFilter.NONE:
            predicates.append(false())
        case unreachable:
            assert_never(unreachable)

    query = (
        select(
            ContentItem,
            Hospital,
            HospitalHandoff,
            assignee,
            task_state.label("task_state"),
            func.count().over().label("_total"),
        )
        .join(Hospital, Hospital.id == ContentItem.hospital_id)
        .outerjoin(HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id)
        .outerjoin(assignee, assignee.id == HospitalHandoff.ae_owner_id)
        .where(*predicates)
    )
    page_stmt = (
        query.order_by(
            ContentItem.published_at.asc().nullslast(), ContentItem.scheduled_date, ContentItem.id
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if overview:
        rows = list((await db.execute(page_stmt)).all())
        total = int(rows[0]._total) if rows else 0
    else:
        count_stmt = (
            select(func.count(ContentItem.id))
            .select_from(ContentItem)
            .join(Hospital, Hospital.id == ContentItem.hospital_id)
            .outerjoin(HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id)
            .outerjoin(assignee, assignee.id == HospitalHandoff.ae_owner_id)
            .where(*predicates)
        )
        total = int((await db.scalar(count_stmt)) or 0)
        rows = list((await db.execute(page_stmt)).all())

    items: list[OperationsQueueRow] = []
    for content, hospital, handoff, actor, state, _total in rows:
        overdue = state == "OVERDUE_REVIEW"
        review = state in {"OVERDUE_REVIEW", "REVIEW_PENDING"}
        occurred_at = content.published_at or content.created_at
        history_at = content.published_at or datetime.combine(
            content.scheduled_date, datetime.min.time(), tzinfo=_SEOUL
        )
        items.append(
            OperationsQueueRow(
                id=f"content:{content.id}",
                queue=OperationsQueue.TODAY,
                customer=OperationsCustomer(
                    hospital_id=hospital.id,
                    name=hospital.name,
                    admin_path=f"/hospitals/{hospital.id}/content",
                ),
                status=state,
                severity="HIGH" if overdue else "MEDIUM",
                impact=(
                    "공개된 콘텐츠의 후행 확인이 지연되고 있습니다."
                    if review
                    else "오늘 발행 예정 콘텐츠가 아직 공개되지 않았습니다."
                ),
                owner=owner_projection(actor),
                sla_due_at=handoff.sla_due_at if handoff else None,
                sla_state="OVERDUE" if overdue else "DUE",
                next_action="공개 내용을 확인해 주세요."
                if review
                else "콘텐츠 상태를 확인해 주세요.",
                action=OperationsAction(
                    kind="REVIEW_CONTENT",
                    label="콘텐츠 확인",
                    method="GET",
                    path=f"/hospitals/{hospital.id}/content?item={content.id}",
                ),
                retry=None,
                safe_cause=None,
                history=[
                    OperationsHistoryEntry(
                        event="PUBLISHED" if review else "SCHEDULED", at=history_at
                    )
                ],
                slack=None,
                content_id=content.id,
                occurred_at=occurred_at,
            )
        )
    return total, items


__all__ = ("load_today_queue",)

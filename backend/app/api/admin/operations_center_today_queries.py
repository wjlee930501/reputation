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
from app.services.post_publish_review_policy import human_post_publish_review_predicate

_OVERDUE_REVIEW_HOURS: Final = 24
_SEOUL: Final = ZoneInfo("Asia/Seoul")
_TODAY_ACTION_LABEL: Final = "콘텐츠 확인"


def _today_operator_copy(*, review: bool) -> tuple[str, str]:
    if review:
        return (
            "자동 검수를 통과해 공개된 콘텐츠의 정기 표본 운영 검수가 남아 있습니다.",
            f"운영 센터의 “{_TODAY_ACTION_LABEL}”에서 표본의 공개 상태와 본문만 확인하세요.",
        )
    return (
        "오늘 발행 예정 글이 아직 병원 채널에 공개되지 않았습니다.",
        f"운영 센터의 “{_TODAY_ACTION_LABEL}”을 눌러 발행 가능한 상태인지 확인하세요. "
        "처리할 버튼이 없으면 개발팀에 병원명과 현재 화면의 문구를 전달하세요.",
    )


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
    waiting_review = human_post_publish_review_predicate()
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
        impact, next_action = _today_operator_copy(review=review)
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
                impact=impact,
                owner=owner_projection(actor),
                sla_due_at=handoff.sla_due_at if handoff else None,
                sla_state="OVERDUE" if overdue else "DUE",
                next_action=next_action,
                action=OperationsAction(
                    kind="REVIEW_CONTENT",
                    label=_TODAY_ACTION_LABEL,
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

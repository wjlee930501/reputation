"""Set-based query builder for the operations-center today queue."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.api.admin.operations_center_query_common import (
    OperationsFilters,
    SlaFilter,
    owner_predicate,
)
from app.api.admin.operations_center_serializers import owner_projection, sla_state
from app.models.admin_user import AdminUser
from app.models.content import ContentItem
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)
from app.services.post_publish_review_policy import (
    auto_publish_due_predicate,
    human_post_publish_review_predicate,
    publicly_operational_hospital_predicate,
)

_OVERDUE_REVIEW_HOURS: Final = 24
_SEOUL: Final = ZoneInfo("Asia/Seoul")
_TODAY_ACTION_LABEL: Final = "콘텐츠 확인"
# The automatic publisher runs at 08:00 KST; before it does, a due slot is not work.
_AUTO_PUBLISH_HOUR: Final = time(8, 0)


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


def operator_visible_publish_due_predicate(
    today: date, now: datetime
) -> ColumnElement[bool]:
    """Return the due-publish rows that are actually waiting on a person.

    A slot scheduled for today is normal state until the 08:00 KST publisher has had
    its turn; showing it as MEDIUM operator work before then invents a task nobody
    can finish. A slot whose scheduled date already passed is real work and stays
    visible at every hour.
    """

    due_publish = auto_publish_due_predicate(today)
    if now.astimezone(_SEOUL).time() < _AUTO_PUBLISH_HOUR:
        return and_(due_publish, ContentItem.scheduled_date < today)
    return due_publish


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
    due_publish = operator_visible_publish_due_predicate(today, now)
    task_state = case(
        (and_(waiting_review, ContentItem.published_at < overdue_before), "OVERDUE_REVIEW"),
        (waiting_review, "REVIEW_PENDING"),
        else_="PUBLISH_DUE",
    )
    severity = case(
        (and_(waiting_review, ContentItem.published_at < overdue_before), "HIGH"),
        else_="MEDIUM",
    )
    # 공개할 수 없는 병원의 슬롯을 사람 업무로 만들지 않는다. 자동 발행 worker도
    # ACTIVE + site_live만 처리하므로, 운영센터가 그보다 넓은 집합을 보여주면 사람에게
    # 영원히 해결할 수 없는 가짜 blocker를 만든다.
    predicates = [
        publicly_operational_hospital_predicate(),
        or_(waiting_review, due_publish),
    ]
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
            # 발행 예정일이 이미 지난 슬롯도 기한을 넘긴 일이다 — 행이 그렇게 표시되므로
            # 필터도 같은 기준을 써야 목록과 필터가 어긋나지 않는다.
            predicates.append(
                or_(
                    and_(waiting_review, ContentItem.published_at < overdue_before),
                    and_(due_publish, ContentItem.scheduled_date < today),
                )
            )
        case SlaFilter.DUE:
            predicates.append(
                or_(
                    and_(waiting_review, ContentItem.published_at >= overdue_before),
                    and_(due_publish, ContentItem.scheduled_date >= today),
                )
            )
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
        # 이 행의 기한은 콘텐츠 작업의 기한이다. 예전에는 계약 인수 기한을 보여 주면서
        # 상태는 발행 후 검수 초과 여부로 정해, 서로 다른 두 기한이 한 줄에 섞였다(G-2).
        # 발행 후 검수는 공개 시각 + 24시간, 발행 예정 글은 예정일이 끝나는 시각이 기한이다.
        if not review:
            due_at = datetime.combine(content.scheduled_date, time.max, tzinfo=_SEOUL)
        elif content.published_at:
            due_at = content.published_at + timedelta(hours=_OVERDUE_REVIEW_HOURS)
        else:
            due_at = None
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
                sla_due_at=due_at,
                sla_state=sla_state(due_at, now),
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


__all__ = ("load_today_queue", "operator_visible_publish_due_predicate")

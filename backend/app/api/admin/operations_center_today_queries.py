"""Set-based query builder for the operations-center today queue."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ARRAY,
    String,
    all_,
    and_,
    any_,
    bindparam,
    case,
    false,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
from app.models.operations import Incident, IncidentState, OperationRun, OperationRunState
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsQueue,
    OperationsQueueRow,
)
from app.services.content_visibility import (
    PublicVisibility,
    assess_sampled_visibility,
    visibility_load_only,
)
from app.services.post_publish_review_policy import (
    auto_publish_due_predicate,
    human_post_publish_review_predicate,
    publicly_operational_hospital_predicate,
)
from app.workers.generation_incident_control import (
    PUBLISHED_IMAGE_RECERTIFY_CODES,
    generation_operator_action,
    generation_safe_cause,
)

_OVERDUE_REVIEW_HOURS: Final = 24
_SEOUL: Final = ZoneInfo("Asia/Seoul")
_TODAY_ACTION_LABEL: Final = "콘텐츠 확인"
_WITHHELD_ACTION_LABEL: Final = "보류 사유 확인"
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


def _blocking_recertify_code(run: OperationRun | None) -> str | None:
    """자동 재인증이 사람 결정으로 끝났을 때만 그 원인 코드를 돌려준다."""
    code = run.safe_error_code if run is not None else None
    return code if code in PUBLISHED_IMAGE_RECERTIFY_CODES else None


def _withheld_next_action(run: OperationRun | None) -> str:
    """보류 행에는 자동 복구가 멈춘 지점의 구체적 조치를 그대로 보여준다."""
    code = _blocking_recertify_code(run)
    if code is not None:
        return generation_operator_action(code)
    return (
        f"운영 센터의 “{_WITHHELD_ACTION_LABEL}”에서 보류 사유를 해소하세요. "
        "사유가 남아 있는 동안에는 공개 내용 확인을 기록할 수 없습니다."
    )


def _withheld_safe_cause(run: OperationRun | None) -> str | None:
    code = _blocking_recertify_code(run)
    return generation_safe_cause(code) if code is not None else None


def publish_due_requires_operator_action(
    scheduled_date: date,
    today: date,
    now: datetime,
    *,
    run_state: str | None = None,
    has_related_incident: bool = False,
) -> bool:
    """Return whether a due-publish slot is work a person must do right now.

    A slot scheduled for today is normal state until the 08:00 KST publisher has had
    its turn; counting it as MEDIUM operator work before then invents a task nobody
    can finish. Dropping it from the response instead was worse — the row vanished
    from the queue and from the totals, so the FE could not collapse what it never
    received. The row ships either way and only this flag changes, which is what
    `OperationsQueueRow.requires_operator_action` and the Slack policy both promise.
    A slot whose scheduled date already passed is real work at every hour.
    """

    if has_related_incident:
        return False
    if scheduled_date < today:
        return True
    if run_state in {
        OperationRunState.REQUESTED.value,
        OperationRunState.QUEUED.value,
        OperationRunState.RUNNING.value,
    }:
        return False
    return now.astimezone(_SEOUL).time() >= _AUTO_PUBLISH_HOUR


async def _withheld_review_samples(
    db: AsyncSession,
    filters: OperationsFilters,
) -> tuple[set[uuid.UUID], dict[uuid.UUID, PublicVisibility]]:
    """Judge the review candidates before the page query so SQL can label them.

    후보는 공개 운영 중인 병원의 후행 검수 표본뿐이라 병원당 소수다. 표본 크기는 검수
    표본 정책(월 시퀀스 1편 + 공개 후 수정, 미확인)이 묶고 AE가 확인할수록 줄어든다.
    그 행에서도 판정에 쓰는 컬럼만 싣는다. 승인 기준 조회는 묶여 있어 병원 수와 무관하게
    2회다. owner 필터는 여기에 적용하지 않는다 — 판정은 담당자와 무관하고, 후보 집합만
    넓어질 뿐 결과는 같기 때문이다.
    """
    candidates = (
        (
            await db.execute(
                select(ContentItem)
                .options(visibility_load_only())
                .join(Hospital, Hospital.id == ContentItem.hospital_id)
                .where(
                    publicly_operational_hospital_predicate(),
                    human_post_publish_review_predicate(),
                    *(
                        [Hospital.id == filters.hospital_id]
                        if filters.hospital_id is not None
                        else []
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    visibility = await assess_sampled_visibility(db, candidates)
    return {
        item_id for item_id, judged in visibility.items() if not judged.visible
    }, visibility


async def load_today_queue(
    db: AsyncSession,
    filters: OperationsFilters,
    *,
    page: int,
    page_size: int,
    overview: bool,
    now: datetime,
) -> tuple[int, list[OperationsQueueRow]]:
    """Load due publishing and post-publication review work for one page.

    Selection, counting and ordering stay set-based. "Already public" is not a column,
    so the bounded review sample is judged first and its withheld ids enter the SQL
    CASE — filters, totals, severity and pagination then read one state.

    The explicit pagination and overview inputs mirror the operations-center HTTP
    contract; their independent meanings make a compact parameter object misleading.
    """
    assignee = aliased(AdminUser)
    related_run = aliased(OperationRun)
    related_incident = aliased(Incident)
    today = now.astimezone(_SEOUL).date()
    overdue_before = now - timedelta(hours=_OVERDUE_REVIEW_HOURS)
    waiting_review = human_post_publish_review_predicate()
    due_publish = auto_publish_due_predicate(today)
    overdue_review = and_(waiting_review, ContentItem.published_at < overdue_before)
    # "공개 중"은 컬럼이 아니라 저장된 행 전체를 다시 판정해야 나오는 사실이다. 그 판정을
    # 페이지를 만든 뒤에 Python에서 덧칠하면 status 필터·total·심각도가 모두 어긋난다 —
    # status=REVIEW_PENDING이 보류 행을 돌려주고, WITHHELD_PUBLIC은 아무것도 못 찾았다.
    # 그래서 검수 후보를 먼저 판정해 그 id를 CASE에 넣고, 선택·집계·정렬을 한 기준으로 둔다.
    # 보류 id는 배열 파라미터 하나로 넘긴다 — 표본이 늘어도 SQL과 파라미터 개수가 같아
    # 준비된 구문이 표본 크기마다 새로 만들어지지 않는다. 부정은 `!= ALL(...)`이어야 한다.
    # `NOT (id = ANY(...))`를 SQLAlchemy가 뒤집으면 `id != ANY(...)`가 되어 뜻이 달라진다.
    withheld_ids, withheld_visibility = await _withheld_review_samples(db, filters)
    withheld_param = bindparam(
        "withheld_ids", value=sorted(withheld_ids), type_=ARRAY(ContentItem.id.type)
    )
    is_withheld = ContentItem.id == any_(withheld_param) if withheld_ids else false()
    not_withheld = ContentItem.id != all_(withheld_param) if withheld_ids else true()
    task_state = case(
        *([(is_withheld, "WITHHELD_PUBLIC")] if withheld_ids else []),
        (overdue_review, "OVERDUE_REVIEW"),
        (waiting_review, "REVIEW_PENDING"),
        else_="PUBLISH_DUE",
    )
    # 기록할 수 없는 검수의 기한 초과를 HIGH로 올리지 않는다 — 할 일은 보류 사유 해소다.
    severity = case((and_(overdue_review, not_withheld), "HIGH"), else_="MEDIUM")
    # 공개할 수 없는 병원의 슬롯을 사람 업무로 만들지 않는다. 자동 발행 worker도
    # ACTIVE + site_live만 처리하므로, 운영센터가 그보다 넓은 집합을 보여주면 사람에게
    # 영원히 해결할 수 없는 가짜 blocker를 만든다.
    predicates = [
        publicly_operational_hospital_predicate(),
        or_(waiting_review, due_publish),
    ]
    if filters.hospital_id is not None:
        predicates.append(Hospital.id == filters.hospital_id)
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
                and_(
                    not_withheld,
                    or_(
                        overdue_review,
                        and_(due_publish, ContentItem.scheduled_date < today),
                    ),
                )
            )
        case SlaFilter.DUE:
            predicates.append(
                and_(
                    not_withheld,
                    or_(
                        and_(waiting_review, ContentItem.published_at >= overdue_before),
                        and_(due_publish, ContentItem.scheduled_date >= today),
                    ),
                )
            )
        case SlaFilter.NONE:
            # 보류 행만 기한이 없다 — 기록할 수 없는 검수에 기한을 붙이지 않기 때문이다.
            predicates.append(is_withheld)
        case unreachable:
            assert_never(unreachable)

    query = (
        select(
            ContentItem,
            Hospital,
            HospitalHandoff,
            assignee,
            related_run,
            related_incident,
            task_state.label("task_state"),
            func.count().over().label("_total"),
        )
        .join(Hospital, Hospital.id == ContentItem.hospital_id)
        .outerjoin(HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id)
        .outerjoin(assignee, assignee.id == HospitalHandoff.ae_owner_id)
        .outerjoin(
            related_run,
            related_run.id
            == select(OperationRun.id)
            .where(
                OperationRun.hospital_id == ContentItem.hospital_id,
                OperationRun.operation_type.in_(
                    (
                        "REGENERATE_CONTENT",
                        "REGENERATE_CONTENT_IMAGE",
                        "RECERTIFY_PUBLISHED_IMAGE",
                    )
                ),
                OperationRun.request_payload["source_id"].as_string()
                == ContentItem.id.cast(String),
            )
            .order_by(OperationRun.requested_at.desc(), OperationRun.id.desc())
            .limit(1)
            .correlate(ContentItem)
            .scalar_subquery(),
        )
        .outerjoin(
            related_incident,
            related_incident.id
            == select(Incident.id)
            .where(
                Incident.hospital_id == ContentItem.hospital_id,
                Incident.source_id == ContentItem.id.cast(String),
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
            .order_by(Incident.last_seen_at.desc(), Incident.id.desc())
            .limit(1)
            .correlate(ContentItem)
            .scalar_subquery(),
        )
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
    for content, hospital, handoff, actor, run, incident, state, _total in rows:
        # 공개 페이지가 숨기는 중인 글에는 "공개 내용 확인"이 성립하지 않는다 — 눌러도 409로
        # 거절되고 행은 큐에 남아 기한만 넘긴다. 사람에게는 다른 일(보류 사유 해소)로 내보낸다.
        withheld = withheld_visibility[content.id] if state == "WITHHELD_PUBLIC" else None
        overdue = state == "OVERDUE_REVIEW"
        review = state in {"OVERDUE_REVIEW", "REVIEW_PENDING"}
        already_public = review or withheld is not None
        # 이 행의 기한은 콘텐츠 작업의 기한이다. 예전에는 계약 인수 기한을 보여 주면서
        # 상태는 발행 후 검수 초과 여부로 정해, 서로 다른 두 기한이 한 줄에 섞였다(G-2).
        # 발행 후 검수는 공개 시각 + 24시간, 발행 예정 글은 예정일이 끝나는 시각이 기한이다.
        # 보류 행에는 기한이 없다 — 지금은 기록할 수 없는 검수의 기한이기 때문이다.
        if withheld is not None:
            due_at = None
        elif not review:
            due_at = datetime.combine(content.scheduled_date, time.max, tzinfo=_SEOUL)
        elif content.published_at:
            due_at = content.published_at + timedelta(hours=_OVERDUE_REVIEW_HOURS)
        else:
            due_at = None
        if withheld is not None:
            impact = "공개 보류 — " + " · ".join(withheld.blocker_labels)
            next_action = _withheld_next_action(run)
            safe_cause = _withheld_safe_cause(run)
        else:
            impact, next_action = _today_operator_copy(review=review)
            safe_cause = None
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
                # 08:00 자동 발행 전의 당일 슬롯은 응답에 남되 사람 업무로 세지 않는다.
                requires_operator_action=(
                    already_public
                    or publish_due_requires_operator_action(
                        content.scheduled_date,
                        today,
                        now,
                        run_state=run.state if run is not None else None,
                        has_related_incident=incident is not None,
                    )
                ),
                action=OperationsAction(
                    kind="REVIEW_CONTENT" if withheld is None else "OPEN_CONTENT",
                    label=_TODAY_ACTION_LABEL if withheld is None else _WITHHELD_ACTION_LABEL,
                    method="GET",
                    path=f"/hospitals/{hospital.id}/content?content={content.id}",
                ),
                retry=None,
                safe_cause=safe_cause,
                history=[
                    OperationsHistoryEntry(
                        event="PUBLISHED" if already_public else "SCHEDULED", at=history_at
                    )
                ],
                slack=None,
                incident_id=incident.id if incident is not None else None,
                operation_run_id=run.id if run is not None else None,
                content_id=content.id,
                occurred_at=occurred_at,
            )
        )
    return total, items


__all__ = ("load_today_queue", "publish_due_requires_operator_action")

"""병원·월의 약속 편수(월 목표) — 첫 계약 월 안분을 한 곳에서 정한다.

요금제 편수(12/16/20)가 기본 약속이다. 다만 시스템이 첫 일정을 월 중간에 만들면 그 달 남은
게시 요일 비율로 편수를 줄이고(`prorated_first_month_quota`), 그 편수를 첫 일정 감사행
(`set_schedule`, 행위자 `system:essence-auto-approval`, `target_id` = 만든 일정)의 detail에
`first_month_target`·`active_from`으로 기록한다(마이그레이션 없는 지속 표식). 그 감사행이
기록한 시작일의 달이 첫 계약 월이고, 그 달 약속은 기록한 편수다(시작일 뒤로 게시 요일이 없으면 0편).
단, 그 달을 계약 월로 둔 살아 있는 슬롯(취소되지 않았거나 이미 공개된 적 있는 글)이 기록한
편수보다 많으면 그 슬롯 수가 약속이다(요금제 편수가 상한). 운영자가 첫 달 배정을 늘려도
감사행은 옛 안분 값으로 남으므로, 그 값만 읽으면 발행 수가 약속을 넘는다("7편 중 12편").

근거는 그 일정을 만든 감사행과 그 달 슬롯의 개수다 — 슬롯의 `total_count`나 병원의 가장 이른
일정을 읽지 않는다. 그래서 사람이 같은 달에 요일·시작일을 바꿔 일정을 교체해도(`POST .../schedule`)
안분 편수는 그대로 남고, 교체 경로도 같은 판정으로 슬롯을 그 편수까지만 만든다(약속이 슬롯
수로 올라가도 이미 있는 슬롯 수까지라 새로 만들 슬롯은 없다). 사람이 만든 첫 일정은 이
감사행이 없으므로 요금제 편수 그대로다(소급하지 않는다).

슬롯 생성(당월 조정 포함)·일정 교체·운영 계약 이행 집계·월간 보고서·현황 카드가 모두 이
판정을 쓴다.
"""

from __future__ import annotations

import math
import uuid
from datetime import date

import arrow
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus, monthly_quota_for_plan

#: 첫 일정을 자동으로 만든 행위자. 안분은 이 경로만 하므로 그 감사행이 안분의 증거다.
INITIAL_SCHEDULE_ACTOR = "system:essence-auto-approval"


def prorated_first_month_quota(plan: object, publish_days: list[int], start: date) -> int | None:
    """첫 계약 월의 약속 편수 = ceil(남은 게시 요일 ÷ 그 달 전체 게시 요일 × 요금제 편수).

    남은 게시 요일이 있으면 최소 1편, 하나도 없으면 0편(그 달은 슬롯 없이 다음 달부터).
    1일에 시작하면 요금제 편수 그대로다. 요금제를 모르면 None.
    """

    quota = monthly_quota_for_plan(plan)
    if quota is None:
        return None
    month = arrow.get(start).floor("month")
    days = [
        day.date()
        for day in arrow.Arrow.range("day", month, month.ceil("month"))
        if day.weekday() in publish_days
    ]
    if not days:
        return quota
    remaining = sum(1 for day in days if day >= start)
    if remaining == 0:
        return 0
    return min(quota, max(1, math.ceil(remaining * quota / len(days))))


def initial_schedule_record_stmt(hospital_id: uuid.UUID):
    """시스템이 만든 첫 일정의 감사행 detail(시작일·첫 달 약속 편수). 사람이 만든 첫 일정이면 행 없음.

    첫 일정 서비스는 일정이 하나도 없는 병원에만 일정을 만들므로 이 행은 병원당 하나다.
    한 번의 왕복만 허용하는 화면(현황)은 이 문장을 `scalar_subquery()`로 기존 조회에 묶는다.
    """

    return (
        select(AdminAuditLog.detail)
        .where(
            AdminAuditLog.hospital_id == hospital_id,
            AdminAuditLog.action == "set_schedule",
            AdminAuditLog.actor == INITIAL_SCHEDULE_ACTOR,
            AdminAuditLog.target_type == "content_schedule",
            AdminAuditLog.target_id.is_not(None),
        )
        .order_by(AdminAuditLog.created_at)
        .limit(1)
    )


def recorded_first_month_target(detail: object, month: arrow.Arrow) -> int | None:
    """첫 일정 감사행이 기록한 첫 달이 `month`이면 그 달 약속 편수, 아니면 None."""

    if not isinstance(detail, dict):
        return None
    target = detail.get("first_month_target")
    if not isinstance(target, int) or isinstance(target, bool) or target < 0:
        return None
    try:
        start = date.fromisoformat(str(detail.get("active_from")))
    except ValueError:
        return None
    if not (month.floor("month").date() <= start <= month.ceil("month").date()):
        return None
    return target


def month_allocation_count_stmt(hospital_id: uuid.UUID, month: arrow.Arrow):
    """그 달을 계약 월로 둔 살아 있는 슬롯 수. 공개된 적 없는 취소 슬롯은 세지 않는다.

    계약 월은 이월 전 날짜(`carried_over_from`)를 따른다 — 보고서의 약정 발행 집계와 같다.
    """

    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    return select(func.count(ContentItem.id)).where(
        ContentItem.hospital_id == hospital_id,
        contract_date >= month.floor("month").date(),
        contract_date <= month.ceil("month").date(),
        or_(
            ContentItem.status != ContentStatus.CANCELLED,
            func.coalesce(ContentItem.first_published_at, ContentItem.published_at).is_not(None),
        ),
    )


def schedule_change_records_stmt(hospital_id: uuid.UUID):
    """병원의 일정 저장 감사행(시각·detail) — 시작일 미루기가 0편으로 둔 달을 가린다."""

    return (
        select(AdminAuditLog.created_at, AdminAuditLog.detail)
        .where(
            AdminAuditLog.hospital_id == hospital_id,
            AdminAuditLog.action == "set_schedule",
        )
        .order_by(AdminAuditLog.created_at, AdminAuditLog.id)
    )


def month_is_voided(records, month: arrow.Arrow) -> bool:
    """첫 발행 전 시작일 미루기가 그 달을 약속 0편으로 기록했고, 그 뒤 그 달을 다시 연 일정이 없는가.

    기록은 사람의 일정 교체 감사행(`set_schedule`)의 `deferred_start.voided_months`다
    (`schedule_reconciliation.defer_unstarted_contract`). 그 뒤에 시작일이 그 달 안이나 앞인
    일정을 다시 저장했으면 그 달은 다시 약속 달이다. `records`는 시각 순서의 (시각, detail).
    """

    key = month.floor("month").format("YYYY-MM")
    month_end = month.ceil("month").date()
    voided = False
    for _created_at, detail in records:
        if not isinstance(detail, dict):
            continue
        deferred = detail.get("deferred_start")
        if isinstance(deferred, dict) and key in (deferred.get("voided_months") or []):
            voided = True
            continue
        try:
            start = date.fromisoformat(str(detail.get("active_from")))
        except ValueError:
            continue
        if start <= month_end:
            voided = False
    return voided


def resolve_month_target(
    quota: int | None,
    first_month_target: int | None,
    allocated: int = 0,
    *,
    voided: bool = False,
) -> int | None:
    """요금제 편수와 첫 일정이 기록한 첫 달 약속 편수로 그 달 약속 편수를 정한다.

    기록한 편수가 요금제 편수보다 작을 때만 안분한 달이다(0 포함 — 게시 요일이 남지 않은 첫 달).
    그 달의 살아 있는 슬롯(`allocated`)이 기록한 편수보다 많으면 슬롯 수다(요금제 편수가 상한).
    """

    if quota is None:
        return None
    if voided:
        # 시작을 뒤 달로 미룬 달 — 그 뒤에 살아 있는 슬롯이 생겼으면 그 수까지만 약속이다.
        return min(quota, allocated)
    if first_month_target is not None and first_month_target < quota:
        return min(quota, max(first_month_target, allocated))
    return quota


def month_content_target_sync(
    db: Session, hospital_id: uuid.UUID, month: arrow.Arrow, plan: object
) -> int | None:
    quota = monthly_quota_for_plan(plan)
    if quota is None:
        return None
    if month_is_voided(db.execute(schedule_change_records_stmt(hospital_id)).all(), month):
        allocated = db.scalar(month_allocation_count_stmt(hospital_id, month)) or 0
        return resolve_month_target(quota, None, allocated, voided=True)
    detail = db.scalar(initial_schedule_record_stmt(hospital_id))
    recorded = recorded_first_month_target(detail, month)
    if recorded is None or recorded >= quota:
        return quota
    allocated = db.scalar(month_allocation_count_stmt(hospital_id, month)) or 0
    return resolve_month_target(quota, recorded, allocated)


async def month_content_target(
    db: AsyncSession, hospital_id: uuid.UUID, month: arrow.Arrow, plan: object
) -> int | None:
    """`month_content_target_sync`의 async 판. 사람의 일정 교체 경로가 쓴다."""

    quota = monthly_quota_for_plan(plan)
    if quota is None:
        return None
    if month_is_voided(
        (await db.execute(schedule_change_records_stmt(hospital_id))).all(), month
    ):
        allocated = (
            await db.execute(month_allocation_count_stmt(hospital_id, month))
        ).scalar_one_or_none() or 0
        return resolve_month_target(quota, None, allocated, voided=True)
    detail = (await db.execute(initial_schedule_record_stmt(hospital_id))).scalar_one_or_none()
    recorded = recorded_first_month_target(detail, month)
    if recorded is None or recorded >= quota:
        return quota
    allocated = (
        await db.execute(month_allocation_count_stmt(hospital_id, month))
    ).scalar_one_or_none() or 0
    return resolve_month_target(quota, recorded, allocated)

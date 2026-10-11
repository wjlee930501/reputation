"""병원·월의 약속 편수(월 목표) — 첫 계약 월 안분을 한 곳에서 정한다.

요금제 편수(12/16/20)가 기본 약속이다. 다만 첫 일정이 월 중간에 시작하면 시스템은 그 달
남은 게시 요일 비율로 편수를 줄여 슬롯을 만들고, 그 편수를 슬롯의 `total_count`에 기록한다
(마이그레이션 없는 지속 표식). 병원의 가장 이른 일정이 시작한 달이고 그 달 슬롯이 요금제보다
작은 `total_count`를 기록하고 있을 때만 그 값을 약속 편수로 본다. 사람이 만든 첫 달은 슬롯이
요금제 편수를 그대로 기록하므로 소급해 바뀌지 않는다.

슬롯 생성(당월 조정 포함)·운영 계약 이행 집계·월간 보고서·현황 카드가 모두 이 판정을 쓴다.
"""

from __future__ import annotations

import math
import uuid
from datetime import date

import arrow
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, monthly_quota_for_plan

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


def resolve_month_target(quota: int | None, first_month_recorded_total: int | None) -> int | None:
    """요금제 편수와 첫 계약 월 슬롯이 기록한 편수로 그 달 약속 편수를 정한다."""

    if quota is None:
        return None
    if first_month_recorded_total is not None and 0 < first_month_recorded_total < quota:
        return first_month_recorded_total
    return quota


def first_month_recorded_total_stmt(hospital_id: uuid.UUID, month: arrow.Arrow):
    """이 달이 병원의 첫 일정 시작 달이면 그 달 슬롯이 기록한 최대 `total_count`, 아니면 NULL.

    한 번의 왕복만 허용하는 화면(현황)은 이 문장을 `scalar_subquery()`로 기존 조회에 묶는다.
    """

    start, end = month.floor("month").date(), month.ceil("month").date()
    first_start = (
        select(func.min(ContentSchedule.active_from))
        .where(ContentSchedule.hospital_id == hospital_id)
        .scalar_subquery()
    )
    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    return select(func.max(ContentItem.total_count)).where(
        ContentItem.hospital_id == hospital_id,
        contract_date >= start,
        contract_date <= end,
        first_start >= start,
        first_start <= end,
        _created_by_initial_schedule_service(hospital_id),
    )


def _created_by_initial_schedule_service(hospital_id: uuid.UUID):
    """첫 일정을 시스템이 만들었다는 감사행. 사람이 만든 첫 달(요금제 변경 포함)은 안분이 아니다."""

    return exists().where(
        AdminAuditLog.hospital_id == hospital_id,
        AdminAuditLog.action == "set_schedule",
        AdminAuditLog.actor == INITIAL_SCHEDULE_ACTOR,
    )


def month_content_target_sync(
    db: Session, hospital_id: uuid.UUID, month: arrow.Arrow, plan: object
) -> int | None:
    quota = monthly_quota_for_plan(plan)
    if quota is None:
        return None
    return resolve_month_target(
        quota, db.scalar(first_month_recorded_total_stmt(hospital_id, month))
    )


def first_month_without_publish_days(db: Session, schedule: ContentSchedule, month: arrow.Arrow) -> bool:
    """시스템이 만든 첫 일정의 첫 달이고 시작일 뒤로 게시 요일이 하나도 남지 않았는가.

    그 달은 약속 편수가 0이다 — 매일 당월 조정이 남은 하루에 한 달 편수를 몰아 넣지 않게 한다.
    사람이 만든 일정은 종전대로 남은 날에 계약 편수를 채운다(계약 복구).
    """

    start = schedule.active_from
    if start is None or not (month.floor("month").date() <= start <= month.ceil("month").date()):
        return False
    if prorated_first_month_quota(schedule.plan, list(schedule.publish_days or []), start) != 0:
        return False
    first_start = db.scalar(
        select(func.min(ContentSchedule.active_from)).where(
            ContentSchedule.hospital_id == schedule.hospital_id,
            _created_by_initial_schedule_service(schedule.hospital_id),
        )
    )
    return first_start == start

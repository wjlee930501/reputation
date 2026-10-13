"""Essence가 승인된 병원의 첫 콘텐츠 일정을 계약 요금제와 기본 요일로 만든다.

사람 경로(`POST /admin/hospitals/{id}/schedule`)는 일정 *교체*까지 다루는 async 경로다.
여기는 아직 일정이 한 번도 없던 병원의 *첫* 일정만 만든다 — 교체·재배치가 없으므로
슬롯 생성은 월말 배치와 같은 `create_next_month_slots_for_schedule`을 쓰고, 감사행은
사람 경로와 같은 action·detail 키로 남긴다. 사람은 언제든 콘텐츠 화면에서 교체할 수 있다.
첫 계약 월은 승인 시점부터 남은 게시 요일로 안분한 편수로 시작한다(D3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import arrow
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.services.audit_log import write_audit_log_sync
from app.services.content_month_target import (
    INITIAL_SCHEDULE_ACTOR,
    prorated_first_month_quota,
)
from app.services.enum_values import enum_value
from app.services.essence_readiness import get_essence_readiness_sync
from app.services.hospital_states import schedule_availability_verdict
from app.utils.db_locks import acquire_hospital_advisory_lock_sync

#: 슬롯을 즉시 만들 수 있는 상태(monthly_slots와 같은 집합). PAUSED는 여기 없다.
INITIAL_SCHEDULE_STATUSES: frozenset[HospitalStatus] = frozenset(
    {HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN}
)

#: admin/lib/schedule.ts `DEFAULT_PUBLISH_DAYS_BY_PLAN`과 같은 값(월=0 … 일=6).
#: 요일 수 × 4주가 정확히 계약 편수라 28일짜리 달도 엄격 배치가 된다.
DEFAULT_PUBLISH_DAYS_BY_PLAN: dict[str, tuple[int, ...]] = {
    "PLAN_20": (0, 1, 2, 3, 4),
    "PLAN_16": (0, 1, 2, 3),
    "PLAN_12": (0, 2, 4),
}


def initial_schedule_terms(
    plan: str,
    *,
    today: date,
    contract_effective_on: date | None,
) -> tuple[list[int], date]:
    """첫 일정의 (발행 요일, 시작일).

    시작일은 내일과 계약 효력일 중 늦은 날이다. 월 중간이어도 다음 달로 미루지 않는다 —
    첫 계약 월은 남은 게시 요일 비율로 약속 편수를 줄여 바로 시작한다
    (`content_month_target.prorated_first_month_quota`).
    """

    days = list(DEFAULT_PUBLISH_DAYS_BY_PLAN[plan])
    earliest = today + timedelta(days=1)
    if contract_effective_on is not None and contract_effective_on > earliest:
        earliest = contract_effective_on
    return days, earliest


@dataclass(frozen=True, slots=True)
class InitialScheduleOutcome:
    created: bool
    reason: str
    schedule_id: uuid.UUID | None = None
    active_from: date | None = None
    slots_created: int = 0


def ensure_initial_content_schedule(
    db: Session,
    hospital_id: uuid.UUID,
    *,
    today: date,
    trigger: str,
    actor: str = INITIAL_SCHEDULE_ACTOR,
) -> InitialScheduleOutcome:
    """일정이 한 번도 없던 병원에 계약 요금제 일정을 만든다. 커밋은 호출부가 한다.

    사람 경로와 같은 병원 잠금 아래에서 다시 확인하므로, 같은 순간 사람이 저장한 일정이나
    다른 워커의 실행과 겹쳐도 일정은 하나만 생긴다. 일정 행이 하나라도 있으면(사람이 끈
    일정 포함) 건드리지 않는다 — 명시적으로 끈 일정을 되살리지 않는다.
    """

    from app.workers.monthly_slots import create_next_month_slots_for_schedule

    acquire_hospital_advisory_lock_sync(db, hospital_id)
    hospital = db.execute(
        select(Hospital).where(Hospital.id == hospital_id).with_for_update()
    ).scalar_one_or_none()
    if hospital is None:
        return InitialScheduleOutcome(False, "NOT_FOUND")
    if hospital.schedule_set:
        return InitialScheduleOutcome(False, "ALREADY_SET")
    if hospital.status not in INITIAL_SCHEDULE_STATUSES:
        return InitialScheduleOutcome(False, "STATUS_NOT_ELIGIBLE")
    if db.scalar(
        select(ContentSchedule.id).where(ContentSchedule.hospital_id == hospital_id).limit(1)
    ) is not None:
        return InitialScheduleOutcome(False, "SCHEDULE_EXISTS")
    plan = enum_value(hospital.plan)
    if plan not in DEFAULT_PUBLISH_DAYS_BY_PLAN:
        return InitialScheduleOutcome(False, "PLAN_MISSING")
    # 사람 경로(`_content_schedule_availability`)와 같은 판정을 쓴다.
    readiness = get_essence_readiness_sync(db, hospital_id)
    verdict = schedule_availability_verdict(
        essence_current=readiness.current is not None,
        required_sources=readiness.required_source_count,
        unprocessed_sources=max(
            readiness.required_source_count - readiness.processed_source_count, 0
        ),
        approved_philosophy_exists=readiness.approved is not None,
    )
    if not verdict.available:
        return InitialScheduleOutcome(False, "ESSENCE_NOT_CURRENT")

    effective_at = db.scalar(
        select(HospitalHandoff.contract_effective_at).where(
            HospitalHandoff.hospital_id == hospital_id
        )
    )
    contract_on = arrow.get(effective_at).to("Asia/Seoul").date() if effective_at else None
    publish_days, active_from = initial_schedule_terms(
        plan, today=today, contract_effective_on=contract_on
    )

    schedule = ContentSchedule(
        hospital_id=hospital_id,
        plan=plan,
        publish_days=publish_days,
        active_from=active_from,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    schedule.hospital = hospital
    db.add(schedule)
    db.flush()

    # 첫 계약 월은 남은 게시 요일로 안분한다. 슬롯이 그 편수를 `total_count`로 기록하고,
    # 보고서·현황·계약 이행 집계·당월 조정이 같은 값을 약속 편수로 읽는다.
    # 남은 게시 요일이 없으면(0편) 이번 달 슬롯은 만들지 않고 월말 배치가 다음 달을 만든다.
    first_month_target = prorated_first_month_quota(plan, publish_days, active_from)
    month = arrow.get(active_from).floor("month")
    if first_month_target:
        create_next_month_slots_for_schedule(
            db,
            schedule,
            month,
            month.date(),
            month.ceil("month").date(),
            month_total=first_month_target,
        )
    slots_created = int(
        db.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(ContentItem.schedule_id == schedule.id)
        )
        or 0
    )
    hospital.schedule_set = True
    status_value = enum_value(hospital.status)
    # 사람 경로(`set_schedule`)와 같은 action·target·detail 키에 자동 생성 사실만 더한다.
    write_audit_log_sync(
        db,
        action="set_schedule",
        hospital_id=hospital_id,
        actor=actor,
        target_type="content_schedule",
        target_id=schedule.id,
        detail={
            "plan": plan,
            "publish_days": publish_days,
            "active_from": str(active_from),
            "slots_created": slots_created,
            "slots_rescheduled": 0,
            "future_contract_preserved": False,
            "old_schedule_ids": [],
            "previous_status": status_value,
            "new_status": status_value,
            "trigger": trigger,
            "contract_effective_on": str(contract_on) if contract_on else None,
            "first_month_target": first_month_target,
        },
    )
    db.flush()
    return InitialScheduleOutcome(True, "CREATED", schedule.id, active_from, slots_created)

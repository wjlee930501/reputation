"""병원별 일일 SLO 점검(Full-Automatic W10, 설계 §1.6) — 읽기 전용 집계.

불변식 I1(예정일 발행)·I2(매월 8일 보고서)를 병원마다 하루 한 번 숫자로 본다. 공급자를 호출하지
않고 커밋하지 않는다. 일일 요약(`fleet_heartbeat`)이 놓친 병원만 한 줄씩 싣는다 — 정상 병원은
줄을 만들지 않는다.

- 오늘 발행: 오늘 예정(취소 제외) 슬롯 중 최초 발행 기록이 있는 수. 08시 전에는 아직 첫 발행
  기회가 오지 않았으므로 놓친 것으로 보지 않는다(`contract_delivery_coverage`와 같은 경계).
- 이번 달: 계약 월 약속 편수(`content_month_target`) − 최초 발행 − 남은 일정(오늘 이후 미발행)이
  0보다 크면 남은 일정으로는 채울 수 없다.
- 지난달 보고서: 8일 09:00 KST(`monthly_final_judgment_at`) 전에는 진행 중이다. 그 뒤 서비스
  구간에 든 병원은 전달 게이트를 통과한 보고서가 있어야 한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import arrow
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.services.content_month_target import month_content_target_sync
from app.services.monthly_period import (
    MonthlyPeriodError,
    eligible_hospital_ids,
    monthly_final_judgment_at,
    prior_month_to_close,
)
from app.services.monthly_report_readiness import load_period_report_state
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate
from app.services.schedule_reconciliation import effective_schedules_query

# 일일 요약에 싣는 병원 줄 상한. 나머지는 "그 외 N곳" 한 줄로 접는다(Slack 블록 3000자 안).
SLO_SUMMARY_LINE_LIMIT = 8
FIRST_RELEASE_HOUR_KST = 8


@dataclass(frozen=True, slots=True)
class HospitalSlo:
    hospital_id: uuid.UUID
    hospital_name: str
    due_today: int = 0
    published_today: int = 0
    month_target: int | None = None
    month_published: int = 0
    month_remaining: int = 0
    # None: 아직 판정 시각 전이거나 서비스 구간 밖. True: 전달 게이트를 통과한 보고서가 없다.
    report_missing: bool | None = None

    @property
    def missed_today(self) -> int:
        return max(0, self.due_today - self.published_today)

    @property
    def month_shortfall(self) -> int:
        if self.month_target is None:
            return 0
        return max(0, self.month_target - self.month_published - self.month_remaining)

    @property
    def missed(self) -> bool:
        return bool(self.missed_today or self.month_shortfall or self.report_missing)


def collect_hospital_slo(db: Session, *, now: datetime) -> list[HospitalSlo]:
    if now.tzinfo is None:
        raise ValueError("A timezone-aware observation time is required")
    local = arrow.get(now).to("Asia/Seoul")
    today = local.date()
    start, end = local.floor("month").date(), local.ceil("month").date()
    hospitals = list(
        db.execute(
            select(Hospital.id, Hospital.name)
            .where(publicly_operational_hospital_predicate())
            .order_by(Hospital.name, Hospital.id)
        ).all()
    )
    if not hospitals:
        return []
    ids = [row.id for row in hospitals]
    schedules = {
        schedule.hospital_id: schedule
        for schedule in db.scalars(effective_schedules_query(end))
        if schedule.hospital_id in set(ids)
    }
    first = func.coalesce(ContentItem.first_published_at, ContentItem.published_at)
    delivered = and_(first.is_not(None), first <= now)
    live = ContentItem.status != "CANCELLED"
    released = local.hour >= FIRST_RELEASE_HOUR_KST
    contract_date = func.coalesce(ContentItem.carried_over_from, ContentItem.scheduled_date)
    rows = {
        row.hospital_id: row
        for row in db.execute(
            select(
                ContentItem.hospital_id,
                func.sum(case((and_(ContentItem.scheduled_date == today, live), 1), else_=0)).label(
                    "due_today"
                ),
                func.sum(
                    case((and_(ContentItem.scheduled_date == today, delivered), 1), else_=0)
                ).label("published_today"),
                func.sum(
                    case(
                        (and_(contract_date >= start, contract_date <= end, delivered), 1),
                        else_=0,
                    )
                ).label("month_published"),
                func.sum(
                    case(
                        (
                            and_(
                                contract_date >= start,
                                contract_date <= end,
                                live,
                                or_(first.is_(None), first > now),
                                ContentItem.scheduled_date >= today,
                                ContentItem.scheduled_date <= end,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("month_remaining"),
            )
            .where(ContentItem.hospital_id.in_(ids))
            .group_by(ContentItem.hospital_id)
        ).all()
    }
    report_missing = _missing_prior_month_reports(db, now=now)
    result = []
    for hospital in hospitals:
        row = rows.get(hospital.id)
        schedule = schedules.get(hospital.id)
        target = (
            month_content_target_sync(db, hospital.id, local, schedule.plan)
            if schedule is not None
            else None
        )
        due_today = int(row.due_today or 0) if row is not None else 0
        result.append(
            HospitalSlo(
                hospital_id=hospital.id,
                hospital_name=str(hospital.name or ""),
                # 08시 전에는 오늘 슬롯이 아직 첫 발행 기회를 맞지 않았다.
                due_today=due_today if released else 0,
                published_today=int(row.published_today or 0) if row is not None else 0,
                month_target=target,
                month_published=int(row.month_published or 0) if row is not None else 0,
                month_remaining=int(row.month_remaining or 0) if row is not None else 0,
                report_missing=report_missing.get(hospital.id),
            )
        )
    return result


def _missing_prior_month_reports(db: Session, *, now: datetime) -> dict[uuid.UUID, bool]:
    try:
        period = prior_month_to_close(now)
    except MonthlyPeriodError:
        return {}
    if now < monthly_final_judgment_at(period.year, period.month):
        return {}
    missing: dict[uuid.UUID, bool] = {}
    for hospital_id in eligible_hospital_ids(db, period):
        state = load_period_report_state(db, hospital_id, period.year, period.month)
        missing[hospital_id] = state.report is None or not state.gate.ready
    return missing


def slo_summary_lines(slos: list[HospitalSlo], *, limit: int = SLO_SUMMARY_LINE_LIMIT) -> list[str]:
    """놓친 병원만 한 줄씩. 정상 병원은 줄이 없다."""

    misses = [slo for slo in slos if slo.missed]
    lines = []
    for slo in misses[:limit]:
        parts = []
        if slo.missed_today:
            parts.append(f"오늘 예정 {slo.due_today}편 중 미발행 {slo.missed_today}편")
        if slo.month_shortfall:
            parts.append(
                f"이번 달 약속 {slo.month_target}편 중 남은 일정으로 {slo.month_shortfall}편 부족"
            )
        if slo.report_missing:
            parts.append("지난달 보고서 전달 준비 안 됨")
        lines.append(f"• {slo.hospital_name[:70]} — " + " · ".join(parts))
    if len(misses) > limit:
        lines.append(f"그 외 {len(misses) - limit}곳은 운영센터에서 볼 수 있습니다.")
    return lines

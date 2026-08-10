"""Typed contracts for closing one historical reporting month."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import assert_never
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.monthly_control import HospitalServiceInterval
from app.models.report import MonthlyReport

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class MonthlyPeriod:
    year: int
    month: int
    starts_at: datetime
    ends_at: datetime
    closes_at: datetime


class ReportBuildReason(StrEnum):
    SCHEDULED_CLOSE = "SCHEDULED_CLOSE"
    MANUAL_REBUILD = "MANUAL_REBUILD"
    LATE_DATA_REBUILD = "LATE_DATA_REBUILD"


@dataclass(frozen=True, slots=True)
class ReportVersionPlan:
    version: int
    supersedes_report_id: uuid.UUID | None
    reason_code: ReportBuildReason
    correlation_key: str
    create: bool


class MonthlyPeriodError(RuntimeError):
    """Raised when an operator requests a month that cannot be closed yet."""


def reporting_period(year: int, month: int) -> MonthlyPeriod:
    """Return the exact KST boundaries for one reporting month."""

    if year < 2000 or year > 2200 or month < 1 or month > 12:
        raise MonthlyPeriodError(
            "선택한 연월을 확인할 수 없습니다. 2000년부터 2200년 사이의 월을 선택해 주세요."
        )
    starts_at = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        ends_at = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        ends_at = datetime(year, month + 1, 1, tzinfo=KST)
    return MonthlyPeriod(
        year=year,
        month=month,
        starts_at=starts_at,
        ends_at=ends_at,
        closes_at=ends_at + timedelta(minutes=15),
    )


def prior_month_to_close(now: datetime) -> MonthlyPeriod:
    """Resolve the prior calendar month once its 00:15 KST cutoff has passed."""

    observed_at = _as_kst(now)
    if observed_at.month == 1:
        period = reporting_period(observed_at.year - 1, 12)
    else:
        period = reporting_period(observed_at.year, observed_at.month - 1)
    if observed_at < period.closes_at:
        raise MonthlyPeriodError(
            "지난달 자료가 아직 마감되지 않았습니다. 오늘 00시 15분 이후 다시 실행해 주세요."
        )
    return period


def require_closed_period(year: int, month: int, *, now: datetime) -> MonthlyPeriod:
    """Reject a requested month until its immutable close boundary has passed."""

    period = reporting_period(year, month)
    if _as_kst(now) < period.closes_at:
        raise MonthlyPeriodError(
            "아직 마감되지 않은 월입니다. 해당 월 마감 시각인 00시 15분 이후, "
            "마감이 끝난 지난달까지만 선택해 주세요."
        )
    return period


def service_interval_overlaps(
    period: MonthlyPeriod, *, started_at: datetime, ended_at: datetime | None
) -> bool:
    """Return whether a half-open service interval intersects the month."""

    started = _as_kst(started_at)
    ended = _as_kst(ended_at) if ended_at is not None else None
    return started < period.ends_at and (ended is None or ended > period.starts_at)


def plan_report_version(
    *,
    latest_report_id: uuid.UUID | None,
    latest_version: int | None,
    reason_code: ReportBuildReason,
    correlation_key: str,
) -> ReportVersionPlan:
    """Choose an immutable report version without carrying operator-written text."""

    if not correlation_key.strip():
        raise MonthlyPeriodError(
            "작업 연결 정보가 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요."
        )
    if (latest_report_id is None) != (latest_version is None):
        raise MonthlyPeriodError(
            "기존 리포트 버전을 확인할 수 없습니다. 개발팀에 작업 정보를 전달해 주세요."
        )

    match reason_code:
        case ReportBuildReason.SCHEDULED_CLOSE:
            if latest_version is not None:
                return ReportVersionPlan(
                    latest_version, None, reason_code, correlation_key, False
                )
            return ReportVersionPlan(1, None, reason_code, correlation_key, True)
        case ReportBuildReason.MANUAL_REBUILD:
            if latest_version is None:
                return ReportVersionPlan(1, None, reason_code, correlation_key, True)
        case ReportBuildReason.LATE_DATA_REBUILD:
            if latest_version is None:
                raise MonthlyPeriodError(
                    "새 버전이 대체할 기존 리포트가 없습니다. 먼저 최초 리포트를 만들어 주세요."
                )
        case unreachable:
            assert_never(unreachable)

    assert latest_report_id is not None
    assert latest_version is not None
    return ReportVersionPlan(
        latest_version + 1,
        latest_report_id,
        reason_code,
        correlation_key,
        True,
    )


def eligible_hospital_ids(db: Session, period: MonthlyPeriod) -> Sequence[uuid.UUID]:
    """Select hospitals whose recorded service was effective at any time in the month."""

    return db.scalars(
        select(HospitalServiceInterval.hospital_id)
        .where(
            HospitalServiceInterval.started_at < period.ends_at,
            or_(
                HospitalServiceInterval.ended_at.is_(None),
                HospitalServiceInterval.ended_at > period.starts_at,
            ),
        )
        .distinct()
    ).all()


def lock_report_version_plan(
    db: Session,
    *,
    hospital_id: uuid.UUID,
    period: MonthlyPeriod,
    reason_code: ReportBuildReason,
    correlation_key: str,
) -> ReportVersionPlan:
    """Lock one hospital so report version selection remains race-safe until commit."""

    locked_hospital = db.scalar(
        select(Hospital.id).where(Hospital.id == hospital_id).with_for_update()
    )
    if locked_hospital is None:
        raise MonthlyPeriodError(
            "병원 정보를 찾을 수 없습니다. 목록으로 돌아가 병원을 다시 선택해 주세요."
        )
    latest = db.scalars(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == hospital_id,
            MonthlyReport.period_year == period.year,
            MonthlyReport.period_month == period.month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
        .limit(1)
    ).first()
    return plan_report_version(
        latest_report_id=latest.id if latest is not None else None,
        latest_version=latest.version if latest is not None else None,
        reason_code=reason_code,
        correlation_key=correlation_key,
    )


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MonthlyPeriodError(
            "시간대 정보가 없습니다. 한국 시간 기준으로 다시 시도해 주세요."
        )
    return value.astimezone(KST)

"""One durable daily summary for unresolved prior-month report gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.hospital import Hospital
from app.models.report import MonthlyReport
from app.services.monthly_period import eligible_hospital_ids, prior_month_to_close
from app.services.notification_contracts import NotificationIntent
from app.services.notification_milestone_rendering import (
    RenderedSlackMessage,
    action_block,
    admin_url,
    header_block,
    safe_text,
    section_block,
    validated_message,
)
from app.services.onboarding_notifications import enqueue_onboarding_notification_sync

KST = ZoneInfo("Asia/Seoul")
MONTHLY_REPORT_GAP_SUMMARY_TYPE = "MONTHLY_REPORT_GAP_SUMMARY"


@dataclass(frozen=True, slots=True)
class MonthlyReportGap:
    hospital_name: str
    state: str


def load_monthly_report_gaps(db: Session, now: datetime) -> tuple[str, list[MonthlyReportGap]]:
    """Return only MISSING and COVERAGE_INCOMPLETE facts for the prior month."""

    period = prior_month_to_close(now)
    period_key = f"{period.year:04d}-{period.month:02d}"
    hospital_ids = list(eligible_hospital_ids(db, period))
    if not hospital_ids:
        return period_key, []
    hospitals = list(
        db.scalars(
            select(Hospital)
            .where(Hospital.id.in_(hospital_ids))
            .order_by(Hospital.name, Hospital.id)
        ).all()
    )
    gaps: list[MonthlyReportGap] = []
    for hospital in hospitals:
        report = db.scalars(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == hospital.id,
                MonthlyReport.period_year == period.year,
                MonthlyReport.period_month == period.month,
                MonthlyReport.report_type == "MONTHLY",
            )
            .order_by(MonthlyReport.version.desc())
            .limit(1)
        ).first()
        if report is None:
            gaps.append(MonthlyReportGap(hospital.name, "MISSING"))
        elif report.quality != "COMPLETE":
            gaps.append(MonthlyReportGap(hospital.name, "COVERAGE_INCOMPLETE"))
    return period_key, gaps


def build_monthly_report_gap_summary(
    *, period_key: str, summary_date: str, gaps: list[MonthlyReportGap]
) -> NotificationIntent:
    missing = [gap for gap in gaps if gap.state == "MISSING"]
    incomplete = [gap for gap in gaps if gap.state == "COVERAGE_INCOMPLETE"]
    url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=reports")
    names = " · ".join(safe_text(gap.hospital_name, 60) for gap in gaps[:15])
    if len(gaps) > 15:
        names = f"{names} · 외 {len(gaps) - 15}곳"
    message = validated_message(
        RenderedSlackMessage(
            fallback_text=(
                f"무슨 문제인지: {period_key} 월간 리포트 미해결 {len(gaps)}곳 · "
                f"고객 영향: 미생성 {len(missing)}곳, 측정 미완료 {len(incomplete)}곳 · "
                "지금 할 일: 운영 센터에서 자동 복구 상태 확인 · 처리 기한: 오늘 중"
            ),
            blocks=(
                header_block("monthly_report_gap_header", "월간 리포트 미해결 요약"),
                section_block(
                    "monthly_report_gap_counts",
                    (
                        f"*{period_key}* · 총 {len(gaps)}곳\n"
                        f"미생성 {len(missing)}곳 · 측정 미완료 {len(incomplete)}곳"
                    ),
                ),
                section_block(
                    "monthly_report_gap_hospitals",
                    (
                        f"대상: {names}\n"
                        "시스템이 매월 1~7일 자동 재측정·마감을 계속합니다. "
                        "오늘 자동 복구 뒤에도 남은 상태를 운영 센터에서 확인해 주세요."
                    ),
                ),
                action_block("monthly_report_gap_action", url, "운영 센터에서 확인"),
            ),
            admin_url=url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=f"{MONTHLY_REPORT_GAP_SUMMARY_TYPE}:{period_key}:{summary_date}",
        notification_type=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        message=message,
    )


def enqueue_monthly_report_gap_summary_sync(db: Session, *, now: datetime) -> bool:
    local = now.astimezone(KST)
    if not 1 <= local.day <= 7:
        return False
    period_key, gaps = load_monthly_report_gaps(db, now)
    if not gaps:
        return False
    enqueue_onboarding_notification_sync(
        db,
        build_monthly_report_gap_summary(
            period_key=period_key,
            summary_date=local.date().isoformat(),
            gaps=gaps,
        ),
        now=now,
    )
    return True

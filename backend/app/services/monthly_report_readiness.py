"""One hospital-month's deliverable report state, read from persisted facts only.

전달 가능 여부의 정본은 `monthly_report_delivery_gate`다. 이 모듈은 그 게이트에 넣을 최신 월간
보고서·그 달 매니페스트·원장 artifact를 한 곳에서 읽어, 월간 보고서 안전망과 사고 근거가 같은
판정을 쓰게 한다(`monthly_reports.customer_ready`는 생성 시점 스냅샷이라 쓰지 않는다).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.services.monthly_report_delivery import DeliveryGate, monthly_report_delivery_gate

REPORT_MISSING_GATE = DeliveryGate(
    False, "report_missing", "이 달의 월간 보고서가 아직 없습니다."
)


@dataclass(frozen=True, slots=True)
class PeriodReportState:
    report: MonthlyReport | None
    manifest: MonthlyMeasurementManifest | None
    artifact: MonthlyReportArtifact | None
    gate: DeliveryGate


def latest_monthly_report(
    db: Session, hospital_id: uuid.UUID, year: int, month: int
) -> MonthlyReport | None:
    return db.scalars(
        select(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == hospital_id,
            MonthlyReport.period_year == year,
            MonthlyReport.period_month == month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
        .limit(1)
    ).first()


def load_period_report_state(
    db: Session, hospital_id: uuid.UUID, year: int, month: int
) -> PeriodReportState:
    """그 달 최신 월간 보고서가 원장에게 전달 가능한지 — 게이트와 같은 사실로 판정한다."""

    report = latest_monthly_report(db, hospital_id, year, month)
    manifest = db.scalars(
        select(MonthlyMeasurementManifest).where(
            MonthlyMeasurementManifest.hospital_id == hospital_id,
            MonthlyMeasurementManifest.period_year == year,
            MonthlyMeasurementManifest.period_month == month,
        )
    ).first()
    if report is None:
        return PeriodReportState(None, manifest, None, REPORT_MISSING_GATE)
    artifact = db.scalars(
        select(MonthlyReportArtifact).where(
            MonthlyReportArtifact.report_id == report.id,
            MonthlyReportArtifact.audience == "DOCTOR",
        )
    ).first()
    return PeriodReportState(
        report, manifest, artifact, monthly_report_delivery_gate(report, manifest, artifact)
    )

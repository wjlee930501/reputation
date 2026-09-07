"""Persisted readiness contract shared by monthly report workers and APIs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.services.report_artifact_validation import parse_doctor_artifact_metadata


@dataclass(frozen=True, slots=True)
class DeliveryGate:
    ready: bool
    code: str | None
    message: str | None
    messages: tuple[str, ...] = ()

    @property
    def all_messages(self) -> tuple[str, ...]:
        if self.messages:
            return self.messages
        return (self.message,) if self.message else ()


def safe_local_report_path(pdf_path: str) -> Path | None:
    try:
        report_root = Path(settings.REPORT_OUTPUT_DIR).resolve(strict=False)
        candidate = Path(pdf_path).resolve(strict=False)
    except (OSError, RuntimeError):
        return None

    try:
        candidate.relative_to(report_root)
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def report_pdf_is_ready(report: MonthlyReport) -> bool:
    path = report.pdf_path
    return bool(
        path
        and (str(path).startswith("gs://") or safe_local_report_path(str(path)) is not None)
    )


def monthly_report_delivery_blockers(report: MonthlyReport) -> list[str]:
    """Derive delivery blockers from the report's persisted generation snapshot."""
    blockers: list[str] = []
    if not report_pdf_is_ready(report):
        blockers.append("PDF 다운로드 파일이 준비되지 않았습니다.")

    sov_summary = report.sov_summary if isinstance(report.sov_summary, dict) else {}
    if sov_summary.get("sov_pct") is None:
        blockers.append("AI 언급률 요약이 없습니다.")

    content_summary = (
        report.content_summary if isinstance(report.content_summary, dict) else {}
    )
    if "published_count" not in content_summary:
        blockers.append("월간 콘텐츠 발행 요약이 없습니다.")
    operations_summary = content_summary.get("operations")
    if isinstance(operations_summary, dict):
        for blocker in operations_summary.get("delivery_blockers") or []:
            if isinstance(blocker, str) and blocker:
                blockers.append(blocker)
    else:
        blockers.append("월간 콘텐츠 운영 검수 요약이 없습니다.")

    essence = report.essence_summary if isinstance(report.essence_summary, dict) else {}
    if not essence.get("approved_philosophy_exists"):
        blockers.append("승인된 콘텐츠 운영 기준이 없습니다.")
    if essence.get("source_stale"):
        blockers.append("리포트의 콘텐츠 운영 기준이 현재 자료와 일치하지 않습니다.")

    source_count = essence.get("source_count")
    processed_count = essence.get("processed_source_count")
    if not isinstance(source_count, int) or source_count < 1:
        blockers.append("리포트에 반영된 온보딩 자료가 없습니다.")
    elif processed_count != source_count:
        blockers.append("처리되지 않은 온보딩 자료가 남아 있습니다.")

    if (essence.get("needs_review_content_count") or 0) > 0:
        blockers.append("운영 기준 재검수가 필요한 콘텐츠가 남아 있습니다.")
    if (essence.get("missing_philosophy_content_count") or 0) > 0:
        blockers.append("승인된 운영 기준 없이 생성된 콘텐츠가 남아 있습니다.")
    if essence.get("medical_risk_findings"):
        blockers.append("의료광고 리스크 표현이 발견된 콘텐츠가 있습니다.")
    return blockers


def monthly_doctor_artifact_is_valid(
    report: MonthlyReport, artifact: MonthlyReportArtifact | None
) -> bool:
    if artifact is None:
        return False
    metadata = parse_doctor_artifact_metadata(artifact.validation_metadata)
    return bool(
        artifact.report_id == report.id
        and artifact.audience == "DOCTOR"
        and artifact.path == report.doctor_pdf_path
        and artifact.validated is True
        and len(artifact.sha256) == 64
        and all(character in "0123456789abcdef" for character in artifact.sha256)
        and artifact.byte_size > 0
        and metadata is not None
        and metadata.sha256 == artifact.sha256
        and metadata.byte_size == artifact.byte_size
    )


def monthly_report_delivery_gate(
    report: MonthlyReport,
    manifest: MonthlyMeasurementManifest | None,
    artifact: MonthlyReportArtifact | None,
) -> DeliveryGate:
    """Decide readiness from the same persisted facts in every server process."""
    counts_complete = (
        report.planned_count > 0
        and report.success_count == report.planned_count
        and report.failed_count == 0
    )
    adequacy = (report.sov_summary or {}).get("observation_adequacy")
    sample_complete = report.quality == "COMPLETE" and counts_complete
    sample_limited = (
        report.quality == "DEGRADED"
        and isinstance(adequacy, dict)
        and adequacy.get("status") == "LIMITED"
        and int(adequacy.get("confirmed_slots") or 0) > 0
    )
    if (not sample_complete and not sample_limited) or manifest is None:
        return DeliveryGate(
            False,
            "coverage_incomplete",
            "이번 달 필수 질문 측정이 모두 끝나지 않았습니다.",
        )
    if not (
        manifest.id == report.manifest_id
        and manifest.hospital_id == report.hospital_id
        and manifest.period_year == report.period_year
        and manifest.period_month == report.period_month
    ):
        return DeliveryGate(
            False,
            "manifest_mismatch",
            "이번 달 필수 측정 결과가 이 병원과 보고 기간에 연결되지 않았습니다.",
        )
    if manifest.closed_at is None:
        return DeliveryGate(
            False,
            "manifest_open",
            "이번 달 필수 측정 집계가 아직 끝나지 않았습니다.",
        )
    if artifact is None:
        return DeliveryGate(
            False,
            "doctor_artifact_missing",
            "검증된 원장 보고용 PDF가 없습니다.",
        )
    if not monthly_doctor_artifact_is_valid(report, artifact):
        return DeliveryGate(
            False,
            "doctor_artifact_invalid",
            "원장 보고용 PDF 검증 정보가 유효하지 않습니다.",
        )
    blockers = monthly_report_delivery_blockers(report)
    if blockers:
        return DeliveryGate(False, "report_blocked", blockers[0], tuple(blockers))
    return DeliveryGate(True, None, None)

"""Persisted readiness contract shared by monthly report workers and APIs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from app.core.config import settings
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.models.report import MonthlyReport
from app.services.report_artifact_validation import validate_persisted_doctor_artifact


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


_AVAILABILITY_COUNT_KEYS = (
    "planned_slots",
    "received_answers",
    "confirmed_slots",
    "ambiguous_slots",
    "answer_failed_slots",
    "judgment_failed_slots",
    "pending_slots",
)


def _availability_counts_are_consistent(row: Mapping[str, object]) -> bool:
    values = {key: row.get(key) for key in _AVAILABILITY_COUNT_KEYS}
    present = {key for key, value in values.items() if value is not None}
    if present <= {"confirmed_slots"}:
        value = values["confirmed_slots"]
        return value is None or type(value) is int and value >= 0
    if present and present != set(_AVAILABILITY_COUNT_KEYS):
        return False
    if not present:
        return True
    if any(type(value) is not int or value < 0 for value in values.values()):
        return False
    planned = values["planned_slots"]
    received = values["received_answers"]
    confirmed = values["confirmed_slots"]
    ambiguous = values["ambiguous_slots"]
    answer_failed = values["answer_failed_slots"]
    judgment_failed = values["judgment_failed_slots"]
    pending = values["pending_slots"]
    if planned != confirmed + ambiguous + pending:
        return False
    received_floor = confirmed + ambiguous + judgment_failed
    return (
        received_floor <= received <= planned - answer_failed
        and answer_failed + judgment_failed <= pending
    )


def _availability_platforms_are_consistent(adequacy: Mapping[str, object]) -> bool:
    platforms = adequacy.get("platforms")
    if platforms is None:
        return True
    if not isinstance(platforms, list) or not platforms:
        return False
    if any(not isinstance(row, Mapping) for row in platforms):
        return False
    rows = [row for row in platforms if isinstance(row, Mapping)]
    platform_ids = [row.get("platform") for row in rows]
    if any(not isinstance(value, str) or not value for value in platform_ids):
        return False
    if len(platform_ids) != len(set(platform_ids)):
        return False
    for row in rows:
        if not _availability_counts_are_consistent(row):
            return False
        mentioned = row.get("confirmed_mentioned_count")
        sample = row.get("confirmed_sample_count")
        confirmed = row.get("confirmed_slots")
        if mentioned is not None or sample is not None:
            if (
                type(mentioned) is not int
                or type(sample) is not int
                or mentioned < 0
                or sample < mentioned
                or type(confirmed) is not int
                or sample > confirmed
            ):
                return False
    return all(
        adequacy.get(key) == sum(row.get(key, 0) for row in rows)
        for key in _AVAILABILITY_COUNT_KEYS
    )


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
        path and (str(path).startswith("gs://") or safe_local_report_path(str(path)) is not None)
    )


def monthly_report_delivery_blockers(report: MonthlyReport) -> list[str]:
    """Derive delivery blockers from the report's persisted generation snapshot."""
    blockers: list[str] = []
    if not report_pdf_is_ready(report):
        blockers.append("PDF 다운로드 파일이 준비되지 않았습니다.")

    sov_summary = report.sov_summary if isinstance(report.sov_summary, dict) else {}
    adequacy = sov_summary.get("observation_adequacy")
    if not isinstance(adequacy, Mapping):
        blockers.append("월간 측정 가용성 상태를 확인할 수 없습니다.")
    else:
        if not _availability_counts_are_consistent(
            adequacy
        ) or not _availability_platforms_are_consistent(adequacy):
            blockers.append("월간 측정 가용성 건수의 합계가 계획 표본과 일치하지 않습니다.")
        status = adequacy.get("status")
        confirmed = adequacy.get("confirmed_slots")
        sov_pct = sov_summary.get("sov_pct")
        numeric_sov = (
            isinstance(sov_pct, (int, float))
            and not isinstance(sov_pct, bool)
            and isfinite(sov_pct)
            and 0 <= sov_pct <= 100
        )
        if status == "UNAVAILABLE":
            if confirmed != 0:
                blockers.append("측정 불가 상태의 확정 답변 수가 0이 아닙니다.")
            if sov_pct is not None:
                blockers.append("확정된 답변이 없는 달에는 AI 언급률을 0으로 표시할 수 없습니다.")
        elif status == "LIMITED":
            if not isinstance(confirmed, int) or isinstance(confirmed, bool) or confirmed <= 0:
                blockers.append("제한된 결과에 확정 답변 수가 없습니다.")
            if sov_pct is not None and not numeric_sov:
                blockers.append("제한된 결과의 AI 언급률 값이 올바르지 않습니다.")
        elif status == "COMPLETE":
            if not numeric_sov:
                blockers.append("완료된 측정의 AI 언급률이 없습니다.")
        else:
            blockers.append("지원하지 않는 월간 측정 가용성 상태입니다.")

        comparison = sov_summary.get("comparison")
        top_level_change = sov_summary.get("change_pct")
        comparison_change = (
            comparison.get("change_pct") if isinstance(comparison, Mapping) else None
        )
        if (
            not isinstance(comparison, Mapping)
            or comparison.get("status") != "COMPARABLE"
        ) and (top_level_change is not None or comparison_change is not None):
            blockers.append("비교할 수 없는 표본에는 전월 대비 증감을 표시할 수 없습니다.")

    content_summary = report.content_summary if isinstance(report.content_summary, dict) else {}
    if "published_count" not in content_summary:
        blockers.append("월간 콘텐츠 발행 요약이 없습니다.")
    operations_summary = content_summary.get("operations")
    if not isinstance(operations_summary, dict):
        blockers.append("월간 콘텐츠 운영 검수 요약이 없습니다.")

    essence = report.essence_summary if isinstance(report.essence_summary, dict) else {}
    if essence.get("medical_risk_findings"):
        blockers.append("의료광고 리스크 표현이 발견된 콘텐츠가 있습니다.")
    return blockers


def monthly_report_delivery_warnings(report: MonthlyReport) -> list[str]:
    """Operational gaps disclosed to the AE without rewriting closed-period facts."""
    warnings: list[str] = []
    sov_summary = report.sov_summary if isinstance(report.sov_summary, dict) else {}
    adequacy = sov_summary.get("observation_adequacy")
    if isinstance(adequacy, Mapping) and adequacy.get("status") == "LIMITED":
        warnings.append(
            "일부 반복 측정이 모호하거나 실패해 확정된 표본만으로 제한된 결과를 제공합니다."
        )
    if isinstance(adequacy, Mapping) and adequacy.get("status") == "UNAVAILABLE":
        warnings.append("확정 가능한 측정 표본을 확보하지 못해 언급률을 산출하지 않았습니다.")

    content_summary = report.content_summary if isinstance(report.content_summary, dict) else {}
    operations = content_summary.get("operations")
    if isinstance(operations, Mapping):
        for key in ("delivery_blockers", "delivery_warnings"):
            warnings.extend(
                value
                for value in operations.get(key) or []
                if isinstance(value, str) and value
            )

    essence = report.essence_summary if isinstance(report.essence_summary, dict) else {}
    if not essence.get("approved_philosophy_exists"):
        warnings.append("승인된 콘텐츠 운영 기준이 없습니다.")
    source_count = essence.get("source_count")
    processed_count = essence.get("processed_source_count")
    if not isinstance(source_count, int) or source_count < 1:
        warnings.append("리포트에 반영된 온보딩 자료가 없습니다.")
    elif processed_count != source_count:
        warnings.append("처리되지 않은 온보딩 자료가 남아 있습니다.")
    if (essence.get("needs_review_content_count") or 0) > 0:
        warnings.append("운영 기준 재검수가 필요한 콘텐츠가 남아 있습니다.")
    if (essence.get("missing_philosophy_content_count") or 0) > 0:
        warnings.append("승인된 운영 기준 없이 생성된 콘텐츠가 남아 있습니다.")
    warnings.extend(
        value
        for value in (getattr(report, "delivery_blockers", None) or [])
        if isinstance(value, str) and value != "DOCTOR_ARTIFACT_UNVALIDATED"
    )
    return list(dict.fromkeys(warnings))


def monthly_doctor_artifact_is_valid(
    report: MonthlyReport, artifact: MonthlyReportArtifact | None
) -> bool:
    return validate_persisted_doctor_artifact(report, artifact).valid


def coverage_is_final(report: MonthlyReport) -> bool:
    """이번 달 측정을 '최종'으로 볼 수 있는가 — 전달 게이트·milestone·월간 마감이 같은 정의를 쓴다.

    COMPLETE(전 슬롯 성공) 또는 LIMITED(표본은 부족하지만 확정 슬롯이 있고 관측 적정성이
    LIMITED로 닫힘). 세 곳이 각자 정의하면 전달 가능한 리포트를 계속 '측정 미완료'로
    되돌리거나(M-02), 한 리포트의 게이트 예외가 전 병원의 알림을 멈춘다(H-11).

    '더 기다릴 필요가 있는가'만 판정한다. LIMITED를 COMPLETE와 같은 품질로 표시하는 근거가
    아니며 품질 구분은 `report.quality`와 관측 적정성이 그대로 유지한다.
    """
    summary = report.sov_summary or {}
    if not isinstance(summary, dict):
        return False
    counts = (report.planned_count, report.success_count, report.failed_count)
    if not all(type(value) is int and value >= 0 for value in counts):
        return False
    counts_complete = counts[0] > 0 and counts[1] == counts[0] and counts[2] == 0
    adequacy = summary.get("observation_adequacy")
    if adequacy is not None and not isinstance(adequacy, dict):
        return False
    if isinstance(adequacy, dict):
        if not _availability_counts_are_consistent(
            adequacy
        ) or not _availability_platforms_are_consistent(adequacy):
            return False
        counter_keys = (
            "planned_slots",
            "confirmed_slots",
            "pending_slots",
            "ambiguous_slots",
            "answer_failed_slots",
            "judgment_failed_slots",
            "received_answers",
        )
        if any(
            key in adequacy and (type(adequacy[key]) is not int or adequacy[key] < 0)
            for key in counter_keys
        ):
            return False
        if "planned_slots" in adequacy:
            planned = adequacy["planned_slots"]
            if any(adequacy.get(key, 0) > planned for key in counter_keys[1:]):
                return False
    if report.quality == "COMPLETE":
        if isinstance(adequacy, dict) and adequacy.get("status") not in (None, "LEGACY_UNKNOWN"):
            planned, confirmed = adequacy.get("planned_slots"), adequacy.get("confirmed_slots")
            return bool(
                counts_complete
                and adequacy.get("status") == "COMPLETE"
                and type(planned) is int
                and planned > 0
                and confirmed == planned
                and not any(
                    adequacy.get(key)
                    for key in (
                        "pending_slots",
                        "ambiguous_slots",
                        "answer_failed_slots",
                        "judgment_failed_slots",
                    )
                )
            )
        return counts_complete
    # Older builders persisted a terminal LIMITED/UNAVAILABLE manifest as BLOCKED.
    # The closed frozen facts now decide availability; preserve that history rather
    # than requiring an in-place rewrite solely to use the delivery contract.
    if report.quality not in {"DEGRADED", "BLOCKED"} or not isinstance(adequacy, dict):
        return False
    if adequacy.get("status") == "LIMITED":
        confirmed = adequacy.get("confirmed_slots")
        return type(confirmed) is int and confirmed > 0
    if adequacy.get("status") == "UNAVAILABLE":
        return adequacy.get("confirmed_slots") == 0
    return False


def monthly_report_delivery_gate(
    report: MonthlyReport,
    manifest: MonthlyMeasurementManifest | None,
    artifact: MonthlyReportArtifact | None,
) -> DeliveryGate:
    """Decide readiness from the same persisted facts in every server process."""
    if not coverage_is_final(report) or manifest is None:
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

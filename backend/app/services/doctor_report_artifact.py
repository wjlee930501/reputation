"""Create, verify, and publish the exact doctor-facing report bytes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import arrow

from app.core.config import settings
from app.models.hospital import Hospital
from app.services.doctor_pdf_contracts import DoctorReportView
from app.services.doctor_report_v4 import TEMPLATE_VERSION, DoctorReportV4
from app.services.report_artifact_validation import (
    DoctorPdfExpectation,
    DoctorPdfValidationError,
    PublishedDoctorPdf,
    render_validated_doctor_pdf,
)
from app.services.report_engine import _upload_to_gcs

logger = logging.getLogger(__name__)
_CAVEAT = "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다."


def build_doctor_pdf_expectation(
    view: DoctorReportView | DoctorReportV4, public_url: str
) -> DoctorPdfExpectation:
    """v4 뷰는 부록 B 첫 줄(측정 방법)과 부록 D 둘째 줄(약속하지 않음)을 고정 문구로 요구한다."""
    if isinstance(view, DoctorReportV4):
        return DoctorPdfExpectation(
            hospital_name=view.hospital_name,
            coverage_text=view.appendix.method_lines[0],
            caveat_text=view.appendix.caveat_lines[1],
            public_url=public_url,
            appendix_expected=True,
            template_version=TEMPLATE_VERSION,
        )
    return DoctorPdfExpectation(
        hospital_name=str(view["hospital_name"]),
        coverage_text=str(view["coverage_text"]),
        caveat_text=_CAVEAT,
        public_url=public_url,
        appendix_expected=bool(view.get("appendix_rows")),
    )


def generate_doctor_pdf_report(
    hospital: Hospital,
    report_id: uuid.UUID,
    period_start: datetime,
    view: DoctorReportView,
    public_url: str,
) -> PublishedDoctorPdf:
    """Upload only bytes that passed the doctor-artifact validator."""

    label = arrow.get(period_start).format("YYYY-MM")
    filename = f"{hospital.slug}_{label}_doctor_{report_id}.pdf"
    output_dir = Path(settings.REPORT_OUTPUT_DIR)
    local_pdf_path = output_dir / filename
    expectation = build_doctor_pdf_expectation(view, public_url)
    rendered = render_validated_doctor_pdf(
        view=view, period_label=label, public_url=public_url, expectation=expectation
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        local_pdf_path.write_bytes(rendered.pdf_bytes)
        if sha256(local_pdf_path.read_bytes()).hexdigest() != rendered.sha256:
            raise OSError("written artifact hash mismatch")
        logger.info("Doctor PDF validated and generated: %s", local_pdf_path)
        gcs_path = _upload_to_gcs(local_pdf_path, hospital.slug, filename)
    except Exception as exc:  # noqa: BLE001 - filesystem/GCS boundary becomes one safe failure.
        try:
            local_pdf_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_STORAGE_FAILED",
            "검증된 원장 전달용 PDF를 안전하게 저장하지 못했습니다.",
        ) from exc
    if gcs_path.startswith("gs://"):
        try:
            local_pdf_path.unlink()
        except OSError:
            logger.warning("Validated doctor PDF local cleanup failed")
    return PublishedDoctorPdf(
        path=gcs_path,
        sha256=rendered.sha256,
        byte_size=rendered.byte_size,
        metadata=rendered.metadata,
    )

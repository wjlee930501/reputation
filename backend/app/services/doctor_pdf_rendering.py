"""Render doctor-report HTML after validating its public link boundary."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.doctor_pdf_contracts import (
    DoctorPdfExpectation,
    DoctorPdfValidationError,
    DoctorReportView,
    ValidatedDoctorPdf,
)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def render_validated_doctor_pdf(
    *,
    view: DoctorReportView,
    period_label: str,
    public_url: str,
    expectation: DoctorPdfExpectation,
) -> ValidatedDoctorPdf:
    """Render once, then validate the exact bytes that will be uploaded."""

    if public_url != expectation.public_url or not safe_public_url(public_url):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PUBLIC_URL_INVALID",
            "안전한 병원 공개 주소를 확인하지 못해 PDF 링크를 만들지 않았습니다.",
        )

    try:
        from weasyprint import HTML

        environment = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(enabled_extensions=("html",)),
        )
        html = environment.get_template("doctor_report.html").render(
            view=view,
            period_label=period_label,
            public_url=public_url,
        )
        rendered = HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()
    except Exception as exc:  # noqa: BLE001 - external renderer boundary is translated safely.
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_RENDER_FAILED",
            "원장 전달용 PDF를 만드는 중 오류가 발생했습니다.",
        ) from exc

    from app.services.report_artifact_validation import validate_doctor_pdf

    pdf_bytes = bytes(rendered)
    metadata = validate_doctor_pdf(pdf_bytes, expectation)
    return ValidatedDoctorPdf(
        pdf_bytes=pdf_bytes,
        sha256=metadata.sha256,
        byte_size=metadata.byte_size,
        metadata=metadata,
    )


def safe_public_url(value: str) -> bool:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    parsed = urlsplit(value)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )

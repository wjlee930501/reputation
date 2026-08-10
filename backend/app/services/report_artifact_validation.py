"""Render and validate the one-page PDF that an AE gives to a hospital director."""

from __future__ import annotations

import io
from hashlib import sha256

from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from app.services import doctor_pdf_contracts as _contracts
from app.services.doctor_pdf_rendering import (
    render_validated_doctor_pdf as _render_validated_doctor_pdf,
)

DoctorArtifactMetadata = _contracts.DoctorArtifactMetadata
DoctorPdfExpectation = _contracts.DoctorPdfExpectation
DoctorPdfValidationError = _contracts.DoctorPdfValidationError
PublishedDoctorPdf = _contracts.PublishedDoctorPdf
ValidatedDoctorPdf = _contracts.ValidatedDoctorPdf
render_validated_doctor_pdf = _render_validated_doctor_pdf

DOCTOR_ARTIFACT_VALIDATION_VERSION = "doctor-pdf-v1"
_A4_WIDTH_PT = 595.28
_A4_HEIGHT_PT = 841.89
_PAGE_TOLERANCE_PT = 2.0
def parse_doctor_artifact_metadata(value: object) -> DoctorArtifactMetadata | None:
    try:
        return DoctorArtifactMetadata.model_validate(value)
    except ValidationError:
        return None


def validate_doctor_pdf(
    pdf_bytes: bytes,
    expectation: DoctorPdfExpectation,
) -> DoctorArtifactMetadata:
    """Validate binary PDF facts before any public path is saved."""

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
    except Exception as exc:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_UNREADABLE",
            "원장 전달용 PDF 파일을 열어 확인할 수 없습니다.",
        ) from exc

    if len(reader.pages) != 1:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PAGE_COUNT_INVALID",
            f"원장 전달용 PDF가 1쪽이 아니라 {len(reader.pages)}쪽으로 만들어졌습니다.",
        )

    page = reader.pages[0]
    width = abs(float(page.mediabox.width))
    height = abs(float(page.mediabox.height))
    if not (
        abs(width - _A4_WIDTH_PT) <= _PAGE_TOLERANCE_PT
        and abs(height - _A4_HEIGHT_PT) <= _PAGE_TOLERANCE_PT
    ):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PAGE_SIZE_INVALID",
            "원장 전달용 PDF가 A4 크기로 만들어지지 않았습니다.",
        )

    extracted_text = page.extract_text() or ""
    normalized_text = _normalize_text(extracted_text)
    required = (
        expectation.hospital_name,
        expectation.coverage_text,
        expectation.caveat_text,
    )
    if not all(_normalize_text(text) in normalized_text for text in required):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_REQUIRED_TEXT_MISSING",
            "원장 전달용 PDF에서 병원명, 측정 범위 또는 주의 문구를 확인하지 못했습니다.",
        )

    glyph_count = sum("가" <= character <= "힣" for character in extracted_text)
    if glyph_count < 1:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_KOREAN_GLYPHS_MISSING",
            "원장 전달용 PDF의 한글을 정상적으로 읽을 수 없습니다.",
        )

    pretendard_embedded, pretendard_to_unicode = _pretendard_font_facts(page)
    if not pretendard_embedded:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_FONT_NOT_EMBEDDED",
            "원장 전달용 PDF에 한글 글꼴이 포함되지 않았습니다.",
        )
    if not pretendard_to_unicode:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_KOREAN_MAPPING_MISSING",
            "원장 전달용 PDF의 한글 문자 연결 정보를 확인하지 못했습니다.",
        )

    links = _uri_links(page)
    if expectation.public_url not in links:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_LINK_MISSING",
            "원장 전달용 PDF에서 병원 공개 정보 페이지 링크를 확인하지 못했습니다.",
        )

    digest = sha256(pdf_bytes).hexdigest()
    return DoctorArtifactMetadata(
        validation_version=DOCTOR_ARTIFACT_VALIDATION_VERSION,
        validation_source="SYSTEM",
        page_count=1,
        page_size="A4",
        glyph_count=glyph_count,
        font_family="Pretendard",
        font_embedded=True,
        korean_to_unicode=True,
        link_count=len(links),
        expected_link_present=True,
        required_text_present=True,
        sha256=digest,
        byte_size=len(pdf_bytes),
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _pretendard_font_facts(page: DictionaryObject) -> tuple[bool, bool]:
    resources = page.get("/Resources")
    fonts = resources.get("/Font", {}) if resources else {}
    embedded = False
    to_unicode = False
    for font_ref in fonts.values():
        font = font_ref.get_object()
        base_font = str(font.get("/BaseFont") or "")
        descendants = font.get("/DescendantFonts") or []
        descendant_fonts = [item.get_object() for item in descendants]
        names = [base_font, *(str(item.get("/BaseFont") or "") for item in descendant_fonts)]
        if not any("Pretendard" in name for name in names):
            continue
        to_unicode = to_unicode or font.get("/ToUnicode") is not None
        descriptors = [font.get("/FontDescriptor")]
        descriptors.extend(item.get("/FontDescriptor") for item in descendant_fonts)
        embedded = embedded or any(
            descriptor is not None
            and any(
                key in descriptor.get_object()
                for key in ("/FontFile", "/FontFile2", "/FontFile3")
            )
            for descriptor in descriptors
        )
    return embedded, to_unicode


def _uri_links(page: DictionaryObject) -> tuple[str, ...]:
    links: list[str] = []
    for annotation_ref in page.get("/Annots") or []:
        annotation = annotation_ref.get_object()
        if str(annotation.get("/Subtype")) != "/Link":
            continue
        action = annotation.get("/A")
        action = action.get_object() if action is not None else None
        if action is None or str(action.get("/S")) != "/URI":
            continue
        uri = action.get("/URI")
        if isinstance(uri, str) and uri:
            links.append(uri)
    return tuple(links)

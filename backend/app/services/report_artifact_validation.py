"""Render and validate the one-page PDF that an AE gives to a hospital director."""

from __future__ import annotations

import io
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

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

DOCTOR_ARTIFACT_VALIDATION_VERSION = "doctor-pdf-v2"
_A4_WIDTH_PT = 595.28
_A4_HEIGHT_PT = 841.89
_PAGE_TOLERANCE_PT = 2.0
# 내부용 판본(report.html·lead_report.html)에만 찍히는 표식. 원장용 PDF에서 하나라도
# 읽히면 내부 판본이 섞였거나 내부 섹션(토킹 포인트 등)이 새어 나온 것이다. 필수 문구
# 검사는 "있어야 할 것"만 보므로, 템플릿이 내부 필드를 찍어도 그대로 통과했다.
INTERNAL_ONLY_MARKERS = (
    "내부 검수용",
    "원장 전달 불가",
    "토킹 포인트",
    "AE 전용",
)


@dataclass(frozen=True, slots=True)
class ArtifactValidationResult:
    state: str
    code: str | None
    metadata: DoctorArtifactMetadata | None

    @property
    def valid(self) -> bool:
        return self.state == "VALID"


def validate_persisted_doctor_artifact(
    report: Any, artifact: Any | None
) -> ArtifactValidationResult:
    """Project one persisted artifact identity through the canonical metadata contract."""
    if artifact is None:
        return ArtifactValidationResult("MISSING", "DOCTOR_ARTIFACT_MISSING", None)
    metadata = parse_doctor_artifact_metadata(artifact.validation_metadata)
    identity_valid = bool(
        artifact.report_id == report.id
        and artifact.audience == "DOCTOR"
        and artifact.path == report.doctor_pdf_path
        and artifact.validated is True
        and isinstance(artifact.sha256, str)
        and len(artifact.sha256) == 64
        and all(character in "0123456789abcdef" for character in artifact.sha256)
        and type(artifact.byte_size) is int
        and artifact.byte_size > 0
    )
    metadata_valid = bool(
        metadata is not None
        and _metadata_is_canonical(artifact.validation_metadata, metadata)
        and metadata.sha256 == artifact.sha256
        and metadata.byte_size == artifact.byte_size
    )
    if not identity_valid or not metadata_valid:
        return ArtifactValidationResult("INVALID", "DOCTOR_ARTIFACT_INVALID", metadata)
    return ArtifactValidationResult("VALID", None, metadata)


def _metadata_is_canonical(value: object, metadata: DoctorArtifactMetadata) -> bool:
    if not isinstance(value, dict):
        return False
    canonical = metadata.model_dump(mode="json")
    return value.keys() == canonical.keys() and all(
        type(value[key]) is type(expected) and value[key] == expected
        for key, expected in canonical.items()
    )


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

    v3 = bool(expectation.main_page_texts)
    page_count = len(reader.pages)
    if not 1 <= page_count <= 32:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PAGE_COUNT_INVALID",
            "원장 전달용 PDF의 읽을 수 있는 페이지 수를 확인하지 못했습니다.",
        )

    page = reader.pages[0]
    for sheet in reader.pages:
        width = abs(float(sheet.mediabox.width))
        height = abs(float(sheet.mediabox.height))
        if not (
            abs(width - _A4_WIDTH_PT) <= _PAGE_TOLERANCE_PT
            and abs(height - _A4_HEIGHT_PT) <= _PAGE_TOLERANCE_PT
        ):
            raise DoctorPdfValidationError(
                "DOCTOR_PDF_PAGE_SIZE_INVALID",
                "원장 전달용 PDF가 A4 크기로 만들어지지 않았습니다.",
            )

    extracted_text = "\n".join(sheet.extract_text() or "" for sheet in reader.pages)
    normalized_text = _normalize_text(extracted_text)
    leaked = internal_markers_in(extracted_text)
    if leaked:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_INTERNAL_TEXT_PRESENT",
            "원장 전달용 PDF에 내부용 문구가 들어 있습니다: " + ", ".join(leaked) + ".",
        )
    required = (
        ("hospital_name", expectation.hospital_name),
    )
    required = (
        *required,
        ("coverage_text", expectation.coverage_text),
        ("caveat_text", expectation.caveat_text),
    )
    required = (*required, *(("overview", text) for text in expectation.required_overview_texts))
    required = (
        *required,
        *(("main_fact", text) for page_texts in expectation.main_page_texts for text in page_texts),
        *(("appendix_fact", text) for text in expectation.required_appendix_texts),
    )
    if expectation.period_label is not None:
        required = (*required, ("period", expectation.period_label))
    missing_fields = [
        field_name
        for field_name, text in required
        if _normalize_text(text) not in normalized_text
    ]
    if missing_fields:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_REQUIRED_TEXT_MISSING",
            "원장 전달용 PDF에서 필수 문구를 확인하지 못했습니다: "
            f"{', '.join(missing_fields)}.",
        )

    # Searching each word globally lets an earlier row hide a missing later row.
    # Bind table text to order and occurrence, including repeated labels.
    cursor = 0
    for row in expectation.required_appendix_rows:
        for value in row:
            text = _normalize_text(value)
            position = normalized_text.find(text, cursor)
            if position < 0:
                raise DoctorPdfValidationError("DOCTOR_PDF_APPENDIX_TEXT_MISSING", "질문표의 행별 결과나 순서가 생성 입력과 일치하지 않습니다.")
            cursor = position + len(text)

    glyph_count = sum("가" <= character <= "힣" for character in extracted_text)
    if glyph_count < 1:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_KOREAN_GLYPHS_MISSING",
            "원장 전달용 PDF의 한글을 정상적으로 읽을 수 없습니다.",
        )

    pretendard_embedded, pretendard_to_unicode = _pretendard_font_facts(page)

    links = tuple(link for sheet in reader.pages for link in _uri_links(sheet))
    if any(url not in links for url in expectation.required_links):
        raise DoctorPdfValidationError("DOCTOR_PDF_LINK_MISSING", "공개 글 근거 링크가 누락됐습니다.")
    if expectation.public_url not in links:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_LINK_MISSING",
            "원장 전달용 PDF에서 병원 공개 정보 페이지 링크를 확인하지 못했습니다.",
        )

    digest = sha256(pdf_bytes).hexdigest()
    return DoctorArtifactMetadata(
        validation_version="doctor-pdf-v3" if v3 else DOCTOR_ARTIFACT_VALIDATION_VERSION,
        validation_source="SYSTEM",
        page_count=page_count,
        page_size="A4",
        glyph_count=glyph_count,
        font_family="Pretendard" if pretendard_embedded or pretendard_to_unicode else "OTHER_KOREAN_FONT",
        font_embedded=pretendard_embedded,
        korean_to_unicode=True,
        link_count=len(links),
        expected_link_present=True,
        required_text_present=True,
        sha256=digest,
        byte_size=len(pdf_bytes),
    )


def internal_markers_in(text: str) -> list[str]:
    """원장에게 나가면 안 되는 내부 표식 중 본문에서 읽히는 것."""
    normalized = _normalize_text(text)
    return [marker for marker in INTERNAL_ONLY_MARKERS if _normalize_text(marker) in normalized]


def _normalize_text(value: str) -> str:
    # PDF extraction may insert whitespace inside a word at a visual line wrap.
    return "".join(value.split())


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

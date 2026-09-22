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

DOCTOR_ARTIFACT_VALIDATION_VERSION = "doctor-pdf-v2"
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

    # 본문은 언제나 1쪽이다. 부록(추적 질문 전체표)이 렌더될 때만 2쪽이 되고,
    # 그 사실은 호출부가 기대값으로 못 박는다 — "왜인지 모르게 2쪽"인 PDF는
    # 원장에게 나가면 안 된다.
    v3 = bool(expectation.main_page_texts)
    expected_pages = expectation.expected_page_count if expectation.expected_page_count is not None else (2 if expectation.appendix_expected else 1)
    if (type(expected_pages) is not int or not 1 <= expected_pages <= 32
            or (not v3 and not expectation.appendix_expected and expected_pages != 1)
            or (not v3 and expectation.appendix_expected and expected_pages < 2)
            or (v3 and (len(expectation.main_page_texts) != 3 or expected_pages < 4))):
        raise DoctorPdfValidationError("DOCTOR_PDF_PAGE_COUNT_INVALID", "리포트의 페이지 구성 기준이 올바르지 않습니다.")
    page_count = len(reader.pages)
    if page_count != expected_pages:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PAGE_COUNT_INVALID",
            f"원장 전달용 PDF가 {expected_pages}쪽이 아니라 {page_count}쪽으로 만들어졌습니다.",
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

    extracted_text = page.extract_text() or ""
    normalized_text = _normalize_text(extracted_text)
    required = (
        ("hospital_name", expectation.hospital_name),
    )
    if not v3:
        required = (*required, ("coverage_text", expectation.coverage_text), ("caveat_text", expectation.caveat_text))
    required = (*required, *(("overview", text) for text in expectation.required_overview_texts))
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

    if v3:
        markers = ("01 / 관측과 결론", "02 / 수행과 증거", "03 / 다음 결정")
        for index, required_texts in enumerate(expectation.main_page_texts):
            page_text = _normalize_text(reader.pages[index].extract_text() or "")
            if not required_texts or _normalize_text(markers[index]) not in page_text or any(_normalize_text(text) not in page_text for text in required_texts):
                raise DoctorPdfValidationError("DOCTOR_PDF_MAIN_TEXT_MISSING", f"본문 {index + 1}쪽의 필수 근거가 누락됐습니다.")
    appendix_start = 3 if v3 else 1
    appendix_text = _normalize_text("\n".join(sheet.extract_text() or "" for sheet in reader.pages[appendix_start:]))
    if v3 and any(_normalize_text(text) not in appendix_text for text in (expectation.coverage_text, expectation.caveat_text)):
        raise DoctorPdfValidationError("DOCTOR_PDF_APPENDIX_TEXT_MISSING", "부록의 측정 범위 또는 해석 제한 문구가 누락됐습니다.")
    if any(_normalize_text(text) not in appendix_text for text in expectation.required_appendix_texts):
        raise DoctorPdfValidationError("DOCTOR_PDF_APPENDIX_TEXT_MISSING", "전체 질문표에 포함되어야 하는 결과가 PDF에서 누락됐습니다.")
    # Searching each word globally lets an earlier row hide a missing later row.
    # Bind table text to order and occurrence, including repeated labels.
    cursor = 0
    for row in expectation.required_appendix_rows:
        for value in row:
            text = _normalize_text(value)
            position = appendix_text.find(text, cursor)
            if position < 0:
                raise DoctorPdfValidationError("DOCTOR_PDF_APPENDIX_TEXT_MISSING", "질문표의 행별 결과나 순서가 생성 입력과 일치하지 않습니다.")
            cursor = position + len(text)
    for sheet in reader.pages[1:]:
        embedded, mapped = _pretendard_font_facts(sheet)
        if not embedded or not mapped:
            raise DoctorPdfValidationError("DOCTOR_PDF_APPENDIX_FONT_INVALID", "부록의 한글 글꼴 또는 문자 연결 정보가 올바르지 않습니다.")
        if not any("가" <= char <= "힣" for char in sheet.extract_text() or ""):
            raise DoctorPdfValidationError("DOCTOR_PDF_APPENDIX_TEXT_MISSING", "부록 페이지의 한글을 확인할 수 없습니다.")

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

    links = tuple(link for sheet in reader.pages for link in _uri_links(sheet)) if v3 else _uri_links(page)
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

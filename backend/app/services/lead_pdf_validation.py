"""Verify actual lead pages, fonts, text and contact isolation before storage."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from pypdf import PdfReader
from weasyprint.document import Document

from app.services.lead_proposal import build_lead_proposal
from app.services.report_artifact_validation import (
    _normalize_text,
    _pretendard_font_facts,
    _uri_links,
)

if TYPE_CHECKING:
    from app.services.lead_report import LeadReportPayload


def validate_lead_document(document: Document, payload: LeadReportPayload) -> bytes:
    if not 4 <= len(document.pages) <= 32:
        raise ValueError("LEAD_REPORT_PAGE_COUNT_INVALID")
    for index in range(3):
        page = document.pages[index]
        if any(f"main-{index + 1}-{edge}" not in page.anchors for edge in ("start", "end")):
            raise ValueError("LEAD_REPORT_MAIN_PAGE_OVERFLOW")
    if "lead-details" not in document.pages[3].anchors:
        raise ValueError("LEAD_REPORT_DETAILS_MISSING")
    anchors = {name for page in document.pages for name in page.anchors}
    if "lead-details-end" not in anchors:
        raise ValueError("LEAD_REPORT_DETAILS_MISSING")
    for index in range(len(payload.queries)):
        if not {f"lead-row-{index}", f"lead-row-{index}-end"} <= anchors:
            raise ValueError("LEAD_REPORT_QUERY_MISSING")
    for page in document.pages:
        for name, point in page.anchors.items():
            if name.startswith(("main-", "lead-")):
                x, y = point[:2]
                if not 60 <= x <= page.width - 60 or not 60 <= y <= page.height - 60:
                    raise ValueError("LEAD_REPORT_LAYOUT_OVERFLOW")
    data = bytes(document.write_pdf())
    pages = PdfReader(BytesIO(data), strict=True).pages
    texts = [_normalize_text(page.extract_text() or "") for page in pages]
    proposal = build_lead_proposal(payload)
    summary = [payload.hospital_name, payload.region, proposal.headline, "진단에 사용한 질문"]
    for segment in payload.segments:
        summary.extend(
            (
                segment.vendor_label,
                f"{segment.mentioned} / {segment.measured}" if segment.measured else "확인 불가",
            )
        )
    summary.extend(query.text for query in payload.queries[:3])
    required = (
        tuple(summary),
        tuple(
            value
            for item in proposal.proposals
            for value in (item.question, item.known, item.check, item.action)
        ),
        (
            "첫 달에는 이렇게 시작합니다",
            "정보 정리 · 콘텐츠 운영 · 반복 측정",
            "01 / 자료 정리",
            "02 / 정보 설계",
            "03 / 검수와 발행",
            "04 / 재측정과 보고",
            "계약 범위",
            "STARTER",
            "GROWER",
            "LEADER",
            "월 12편 발행",
            "월 16편 발행",
            "월 20편 발행",
            "월 60만 원 · 부가세 별도",
            "월 90만 원 · 부가세 별도",
            "월 120만 원 · 부가세 별도",
        ),
    )
    for index, values in enumerate(required):
        if any(_normalize_text(value) not in texts[index] for value in values):
            raise ValueError("LEAD_REPORT_REQUIRED_TEXT_MISSING")
    details = "".join(texts[3:])
    if _normalize_text(payload.system_prompt) not in details:
        raise ValueError("LEAD_REPORT_METHOD_MISSING")
    method_values = [
        payload.judge_model,
        f"계획 반복 {payload.repeat_count}회",
        payload.generated_at.strftime("%Y-%m-%d"),
        *payload.notices,
    ]
    for segment in payload.segments:
        method_values.extend(
            (
                segment.label,
                f"계획 {segment.planned} / 확정 {segment.measured} / 언급 {segment.mentioned} / 실패 {segment.failed} / 보류 {segment.ambiguous} / 미실행 {segment.pending}회",
                f"검색 사용 {segment.searched if segment.searched is not None else '기록 없음'}",
                f"확정 반복 기준 {str(segment.mention_rate) + '%' if segment.mention_rate is not None else '측정 미완료'}",
            )
        )
        if segment.rate_ceiling is not None:
            method_values.append(f"미확정이 모두 언급일 때 {segment.rate_ceiling}%")
    if any(_normalize_text(value) not in details for value in method_values):
        raise ValueError("LEAD_REPORT_METHOD_MISSING")
    cursor = 0
    for query in payload.queries:
        for value in (
            query.text,
            f"{query.measured} / {query.mentioned}",
            f"{query.failed} / {query.ambiguous}",
            query.measured_at.strftime("%Y-%m-%d") if query.measured_at else "없음",
        ):
            needle = _normalize_text(value)
            position = details.find(needle, cursor)
            if position < 0:
                raise ValueError("LEAD_REPORT_QUERY_MISSING")
            cursor = position + len(needle)
    for page in pages:
        if _pretendard_font_facts(page) != (True, True):
            raise ValueError("LEAD_REPORT_FONT_INVALID")
    links = tuple(link for page in pages for link in _uri_links(page))
    if payload.internal:
        if links or "AE전용" not in "".join(texts):
            raise ValueError("LEAD_REPORT_INTERNAL_CONTACT")
    else:
        expected = (
            f"tel:{payload.contact.phone}"
            if payload.contact.phone
            else f"mailto:{payload.contact.email}"
            if payload.contact.email
            else None
        )
        if set(links) != ({expected} if expected else set()):
            raise ValueError("LEAD_REPORT_CONTACT_INVALID")
    return data

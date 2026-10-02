"""V3 page-specific evidence contract and rendered geometry checks."""

from dataclasses import replace
from math import isfinite
from typing import Protocol

from app.services.doctor_pdf_contracts import (
    DoctorPdfExpectation,
    DoctorPdfValidationError,
    DoctorReportView,
)


def validate_v3_fields(view: DoctorReportView) -> None:
    n = view["narrative"]
    values = [
        view["hospital_name"],
        view["coverage_text"],
        n.title,
        n.conclusion,
        n.comparison_note,
        *n.priorities,
        *n.methods,
        *n.citation_details,
    ]
    values.extend(work.title for work in n.works)
    for case in view["evidence"].values():
        if case:
            values.extend((case["question"], case["excerpt"]))
    if any(
        len(value) > 2000 or any(ord(char) < 32 and char not in "\n\t\r" for char in value)
        for value in values
    ):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_LAYOUT_LIMIT", "지원 길이나 문자 범위를 벗어난 보고서 필드입니다."
        )
    if any(
        value is not None and (not isfinite(value) or not 0 <= value <= 100)
        for value in (n.current, n.previous)
    ):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_METRIC_INVALID", "측정 비율이 유효 범위를 벗어났습니다."
        )


class RenderedPage(Protocol):
    width: float
    height: float
    anchors: dict[str, tuple[float, float, float, float]]


class RenderedDocument(Protocol):
    pages: list[RenderedPage]


def v3_layout_valid(document: RenderedDocument, row_count: int) -> bool:
    if not 4 <= len(document.pages) <= 32:
        return False
    for index in range(3):
        anchors = document.pages[index].anchors
        if any(f"main-{index + 1}-{edge}" not in anchors for edge in ("start", "end")):
            return False
    if "appendix-start" not in document.pages[3].anchors:
        return False
    anchors = {name for page in document.pages for name in page.anchors}
    if "appendix-end" not in anchors:
        return False
    for index in range(row_count):
        if not {f"appendix-row-{index}", f"appendix-row-{index}-end"} <= anchors:
            return False
    for page in document.pages:
        for name, point in page.anchors.items():
            if name.startswith(("main-", "appendix-")):
                x, y = point[:2]
                if not 60 <= x <= page.width - 60 or not 60 <= y <= page.height - 60:
                    return False
    return True


def v3_expectation(
    view: DoctorReportView,
    expectation: DoctorPdfExpectation,
    page_count: int,
) -> DoctorPdfExpectation:
    n = view["narrative"]
    initial = view.get("report_kind") == "INITIAL"
    tile = view["tiles"][0]
    page1 = [
        n.title,
        n.conclusion,
        n.denominator,
        "AI에게 물었을 때 우리 병원이 언급된 비율",
        "지난달",
        "이번 달",
        "AI가 언급한 질문",
        "AI가 참고한 우리 글",
        "다음 달에 할 일",
        n.priorities[0],
        f"{n.current:.1f}%" if n.current is not None else n.current_label,
        (
            f"{n.previous:.1f}%"
            if n.previous is not None
            else f"{n.reference_previous:.1f}%"
            if n.reference_previous is not None
            else n.previous_label
        ),
    ]
    if n.previous is None and n.current is not None:
        page1.append(n.comparison_note)
    if n.current is not None and round(n.current) > 0:
        page1.append(f"100번 물으면 약 {round(n.current)}번 우리 병원이 언급된 셈입니다.")
    if not initial:
        page1.extend((tile["label"], tile["value"]))
    if view.get("v0_baseline"):
        base = view["v0_baseline"]
        page1.append(f"처음 측정 {base['of_hundred']}% / 이번 달 {base['current_of_hundred']}%")
    page2 = ["첫 측정에서", "확인한 내용"] if initial else ["이번 달 한 일을", "보고드립니다", "그 결과"]
    page2.append("AI 답변 속 우리 병원")
    for work in n.works[:2]:
        page2.append(work.title)
        page2.append(work.citation_label)
        if work.queries:
            page2.append(work.queries[0])
    for case in view["evidence"].values():
        if case:
            excerpt = case["excerpt"]
            preview = excerpt[:130] + "…" if len(excerpt) > 130 else excerpt
            page2.extend((case["question"], preview, case["platform"]))
    page3 = [
        "다음 달에 할 일",
        tile["label"],
        tile["value"],
        tile["hint"],
        n.fulfillment_note,
        "담당 마케터는 이렇게 진행합니다",
        *n.priorities[:3],
    ]
    if initial:
        page3 = ["앞으로 할 일", "첫 측정 보고서입니다", *n.priorities[:3]]
    appendix = list(expectation.required_appendix_texts)
    for case in view["evidence"].values():
        if case:
            appendix.extend((case["question"], case["excerpt"], case["platform"]))
    appendix.extend(view["footnotes"])
    appendix.extend((*n.methods, *n.platform_details, n.comparison_note, n.citation_scope))
    appendix.extend(n.citation_details)
    appendix.extend(n.priorities[3:])
    for work in n.works:
        appendix.extend((work.title, work.appendix_label, *work.queries))
    for item in (*view["new_mention_sentences"], *view["lost_mention_sentences"]):
        appendix.extend((item["query_text"], item["platform_label"]))
    return replace(
        expectation,
        expected_page_count=page_count,
        main_page_texts=(tuple(page1), tuple(page2), tuple(page3)),
        required_overview_texts=(),
        required_appendix_texts=tuple(appendix),
        required_links=tuple(work.url for work in n.works if work.url),
    )

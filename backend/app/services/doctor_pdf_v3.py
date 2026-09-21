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
    page1 = [n.title, n.conclusion, n.denominator, "지난달", "이번 달"]
    if n.previous is None:
        page1.append(n.comparison_note)
    elif n.current is not None:
        page1.append(f"{n.current - n.previous:+.1f}%p")
    page1.extend(
        (
            f"{n.current:.1f}%" if n.current is not None else "확인 불가",
            f"{n.previous:.1f}%" if n.previous is not None else "비교 보류",
        )
    )
    if view.get("v0_baseline"):
        base = view["v0_baseline"]
        page1.append(f"최초 측정 {base['of_hundred']}% / 이번 관측 {base['current_of_hundred']}%")
    page2 = (
        ["최초 관측과", "확인한 근거"]
        if view.get("report_kind") == "INITIAL"
        else ["이번 달 만든 정보와", "활용 결과"]
    )
    for work in n.works[:2]:
        page2.append(work.title)
        page2.append(
            f"출처로 확인 · {work.cited_cells}개 질문×플랫폼 조합"
            if work.cited_cells
            else "출처 연결은 아직 확인되지 않았습니다"
        )
        if work.queries:
            page2.append(work.queries[0])
    for case in view["evidence"].values():
        if case:
            excerpt = case["excerpt"]
            preview = excerpt[:130] + "…" if len(excerpt) > 130 else excerpt
            page2.extend((case["question"], preview, case["platform"]))
    page3 = [
        "다음 달 집중할 진료 질문",
        "이번 달 약정 이행",
        view["tiles"][0]["label"],
        view["tiles"][0]["value"],
        view["tiles"][0]["hint"],
        n.fulfillment_note,
        *n.priorities[:3],
    ]
    if view.get("report_kind") == "INITIAL":
        page3 = ["최초 관측 기록", *n.priorities[:3]]
    appendix = list(expectation.required_appendix_texts)
    for case in view["evidence"].values():
        if case:
            appendix.extend((case["question"], case["excerpt"], case["platform"]))
    appendix.extend(view["footnotes"])
    appendix.extend((*n.methods, *n.platform_details, n.comparison_note, n.citation_scope))
    appendix.extend(n.citation_details)
    appendix.extend(n.priorities[3:])
    for work in n.works:
        appendix.extend((work.title, (f"소유 URL 인용 {work.cited_cells}개 조합" if work.cited_cells is not None else "소유 URL 인용 미확인"), *work.queries))
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

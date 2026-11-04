"""원장용 월간 보고서 v4 — 렌더 검증 계약(레이아웃 앵커·필수 문구).

설계 §6(`docs/plans/2026-11-04-doctor-report-v4-design.md`). 필수 문구는 뷰 모델
(`DoctorReportV4.required_texts`)에서 뽑는다 — 이 파일에 문장을 손으로 복사하지 않는다.

본문 6절은 저마다 새 쪽에서 시작하고 길면 다음 쪽으로 흐른다(2026-11 대표 결정: 행을 줄여 한 쪽에
욱여넣지 않는다). 그래서 쪽 묶음 검사는 '절 n = 쪽 n'이 아니라 렌더된 문서의 앵커로 절의 쪽 범위를 읽어
그 범위 안에서 문구가 실제로 놓인 쪽에 묶는다(`page_bound_texts`). 렌더러는 `v4_layout_valid` 뒤에
`v4_expectation`을 부르므로 마지막으로 통과한 문서의 쪽 지도를 스레드 로컬로 넘긴다.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, fields, is_dataclass, replace
from math import isfinite
from typing import Any

from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
from app.services.doctor_pdf_v3 import RenderedDocument
from app.services.doctor_report_v4 import TEMPLATE_VERSION, DoctorReportV4

BODY_PAGES = 6
MIN_PAGES = BODY_PAGES + 1
# 절이 흐르므로 상한은 14쪽(대표 결정) — 그 이상은 입력이 비정상이다.
MAX_PAGES = 14
# WeasyPrint의 쪽·앵커 좌표는 CSS px(A4 = 793.7 × 1122.5px)이고 앵커 상자는 (x1, y1, x2, y2)다.
# 인쇄 가능 영역은 report_editorial.css의 @page 여백(옆 17mm · 머리 18mm · 발 20mm)이다 — 옆 레일이
# 48pt라 60pt(80px) 여백은 왼쪽 레일에 붙은 모든 앵커를 거절하므로 실제 여백을 쓴다. 상자의
# 네 변이 모두 그 안에 있어야 한다(러닝 헤드·풋 영역으로 넘친 내용을 잡는다).
_PX_PER_MM = 96 / 25.4
_RAIL_X_PX = 17 * _PX_PER_MM
_HEAD_PX = 18 * _PX_PER_MM
_FOOT_PX = 20 * _PX_PER_MM
_TOLERANCE_PX = 1.0
_MAX_FIELD_CHARS = 2000
_WHITESPACE = re.compile(r"\s+")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if is_dataclass(value) and not isinstance(value, type):
        return [s for f in fields(value) for s in _strings(getattr(value, f.name))]
    if isinstance(value, (tuple, list)):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def validate_v4_fields(view: DoctorReportV4) -> None:
    """길이·제어 문자·비율 범위를 렌더 전에 거른다(v3와 같은 한계)."""
    values = _strings(view)
    if any(
        len(value) > _MAX_FIELD_CHARS or any(ord(char) < 32 and char not in "\n\t\r" for char in value)
        for value in values
    ):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_LAYOUT_LIMIT", "지원 길이나 문자 범위를 벗어난 보고서 필드입니다."
        )
    rates = [view.current_rate, view.previous_rate, view.reference_previous_rate]
    rates.extend(bar.value for bar in view.exposure.platform_bars)
    rates.extend(bar.value for bar in view.exposure.treatment_bars)
    rates.extend(row.rate for row in view.exposure.group_rows)
    rates.extend(point.value for point in view.exposure.trend)
    rates.extend(row.rate for row in view.competition.rows)
    if any(value is not None and (not isfinite(value) or not 0 <= value <= 100) for value in rates):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_METRIC_INVALID", "측정 비율이 유효 범위를 벗어났습니다."
        )
    if len(view.appendix.keyword_rows) > 512 or len(view.content.rows) > 512:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_LAYOUT_LIMIT",
            "키워드표가 지원 문서 크기를 넘었습니다. 결과를 누락하지 않고 중단했습니다.",
        )


@dataclass(frozen=True)
class SectionPages:
    """절 n(1~6)이 차지하는 쪽 범위(0부터, 양 끝 포함)와 부록 첫 쪽."""

    body: tuple[tuple[int, int], ...]
    appendix_start: int


def _anchor_page(document: RenderedDocument, name: str) -> int | None:
    for index, page in enumerate(document.pages):
        if name in page.anchors:
            return index
    return None


def section_pages(document: RenderedDocument) -> SectionPages | None:
    """앵커로 절의 쪽 범위를 읽는다. 순서가 어긋나면 None.

    규칙: p1-start는 0쪽, p{n}-start는 오름차순, p{n}-end는 자기 start 쪽 이상·p{n+1}-start 쪽 미만
    (같은 쪽에 두 절이 함께 있지 않다 — 절은 새 쪽에서 시작한다), appendix-start는 p6-end 뒤 쪽.
    """
    ranges: list[tuple[int, int]] = []
    for n in range(1, BODY_PAGES + 1):
        start = _anchor_page(document, f"p{n}-start")
        end = _anchor_page(document, f"p{n}-end")
        if start is None or end is None or end < start:
            return None
        if n == 1 and start != 0:
            return None
        if ranges and start <= ranges[-1][1]:
            return None
        ranges.append((start, end))
    appendix_start = _anchor_page(document, "appendix-start")
    if appendix_start is None or appendix_start <= ranges[-1][1]:
        return None
    return SectionPages(body=tuple(ranges), appendix_start=appendix_start)


def _page_text(page: Any) -> str:
    """WeasyPrint 쪽 상자의 글자를 공백 없이 잇는다(pypdf 추출과 같은 squash 비교용)."""
    parts: list[str] = []

    def walk(box: Any) -> None:
        text = getattr(box, "text", None)
        if isinstance(text, str):
            parts.append(text)
        for child in getattr(box, "children", ()):
            walk(child)

    root = getattr(page, "_page_box", None)
    if root is not None:
        walk(root)
    return _WHITESPACE.sub("", "".join(parts))


_last_layout = threading.local()


TABLE_ANCHORS = ("p2-table", "p3-heat", "p4-table", "p6-table", "appendix-table")


def required_table_anchors(view: DoctorReportV4) -> frozenset[str]:
    """뷰가 그리는 넓은 표마다 앵커 상자 하나 — 렌더러가 `v4_layout_valid(required_anchors=)`로 넘긴다."""
    names: set[str] = set()
    if view.content.rows:
        names.add("p2-table")
    if view.exposure.heatmap:
        names.add("p3-heat")
    if view.competition.rows:
        names.add("p4-table")
    if view.plan.rows:
        names.add("p6-table")
    if view.appendix.keyword_rows:
        names.add("appendix-table")
    return frozenset(names)


def v4_layout_valid(
    document: RenderedDocument, keyword_row_count: int, *, required_anchors: frozenset[str] = frozenset()
) -> bool:
    """본문 6절은 저마다 새 쪽에서 시작해 순서대로 흐르고, 부록은 마지막 절 뒤에 이어진다.

    `p`·`appendix-`·`kw-row-`로 시작하는 모든 앵커가 레일 검사 대상이다 — 절 앵커 `p{n}-start/end`뿐 아니라
    넓은 표를 감싸는 앵커 상자 `p2-table`·`p3-heat`·`p4-table`·`p6-table`·`appendix-table`(shrink-to-fit
    상자라 표가 레일보다 넓어지면 사각형도 넓어진다)도 포함이라 표가 오른쪽 레일을 넘으면 거절된다.
    `required_anchors`에 든 이름은 문서에 반드시 있어야 한다(표를 그렸는데 앵커가 빠지면 거절). 통과하면
    쪽 지도(절별 쪽 범위와 쪽별 글자)를 스레드 로컬에 남겨 `v4_expectation`이 쪽 묶음 문구를 만든다.
    """
    if not MIN_PAGES <= len(document.pages) <= MAX_PAGES:
        return False
    sections = section_pages(document)
    if sections is None:
        return False
    anchors = {name for page in document.pages for name in page.anchors}
    if "appendix-end" not in anchors:
        return False
    if not required_anchors <= anchors:
        return False
    for index in range(keyword_row_count):
        if not {f"kw-row-{index}", f"kw-row-{index}-end"} <= anchors:
            return False
    for page in document.pages:
        left, top = _RAIL_X_PX - _TOLERANCE_PX, _HEAD_PX - _TOLERANCE_PX
        right, bottom = page.width - _RAIL_X_PX + _TOLERANCE_PX, page.height - _FOOT_PX + _TOLERANCE_PX
        for name, box in page.anchors.items():
            if name.startswith(("p", "appendix-", "kw-row-")):
                x1, y1 = box[0], box[1]
                x2 = box[2] if len(box) > 2 else x1
                y2 = box[3] if len(box) > 3 else y1
                if not left <= x1 <= x2 <= right:
                    return False
                # 표 앵커 상자는 쪽을 넘어 이어질 수 있어(WeasyPrint는 첫 조각의 사각형에 전체 높이를 준다)
                # 세로는 보지 않는다 — 세로 넘침은 쪽 나눔이 처리하고 행 앵커(kw-row)가 따로 잡는다.
                if name in TABLE_ANCHORS:
                    continue
                if not top <= y1 <= y2 <= bottom:
                    return False
    _last_layout.sections = sections
    _last_layout.page_texts = tuple(_page_text(page) for page in document.pages)
    return True


def page_bound_texts(
    view: DoctorReportV4, sections: SectionPages, page_texts: tuple[str, ...]
) -> tuple[tuple[str, ...], ...]:
    """절의 필수 문구를 그 절의 쪽 범위 안에서 실제로 놓인 쪽에 묶는다(쪽마다 튜플 하나, 부록 앞까지).

    한 쪽짜리 절은 종전처럼 문구 전부를 그 쪽에 묶고, 여러 쪽에 걸친 절은 쪽별 글자(공백 제거)에서 처음
    찾은 쪽에 묶는다. 쪽 상자의 글자에 없는 문구(추이 SVG의 값처럼 TextBox가 아닌 글자)는 절의 첫 쪽에
    묶는다 — 이진 검증(pypdf 추출)이 그 쪽에서 다시 확인하므로 느슨해지지 않는다.
    """
    texts = view.required_texts()
    bound: list[list[str]] = [[] for _ in range(sections.appendix_start)]
    for n, (start, end) in enumerate(sections.body):
        if start == end:
            bound[start].extend(texts[n])
            continue
        for text in texts[n]:
            squashed = _WHITESPACE.sub("", text)
            for page in range(start, end + 1):
                if page < len(page_texts) and squashed in page_texts[page]:
                    bound[page].append(text)
                    break
            else:
                bound[start].append(text)
    return tuple(tuple(page) for page in bound)


def v4_expectation(
    view: DoctorReportV4, expectation: DoctorPdfExpectation, page_count: int
) -> DoctorPdfExpectation:
    texts = view.required_texts()
    main_page_texts: tuple[tuple[str, ...], ...] = texts[:BODY_PAGES]
    sections = getattr(_last_layout, "sections", None)
    page_texts = getattr(_last_layout, "page_texts", ())
    if sections is not None and len(page_texts) == page_count:
        main_page_texts = page_bound_texts(view, sections, page_texts)
    return replace(
        expectation,
        expected_page_count=page_count,
        main_page_texts=main_page_texts,
        required_appendix_texts=(*expectation.required_appendix_texts, *texts[BODY_PAGES]),
        # 부록 A는 행 단위·순서대로 묶어 검사한다 — 칸 하나가 바뀌거나 행끼리 바뀌면 통과하지 못한다.
        required_appendix_rows=tuple(
            (row.keyword, row.group, row.treatment, *row.cells, row.total, *((row.previous,) if row.previous else ()))
            for row in view.appendix.keyword_rows
        ),
        required_links=view.required_links(),
        required_overview_texts=(),
        template_version=TEMPLATE_VERSION,
    )


__all__ = [
    "TABLE_ANCHORS",
    "SectionPages",
    "required_table_anchors",
    "page_bound_texts",
    "section_pages",
    "v4_expectation",
    "v4_layout_valid",
    "validate_v4_fields",
]

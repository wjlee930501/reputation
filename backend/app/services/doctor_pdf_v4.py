"""원장용 월간 보고서 v4 — 렌더 검증 계약(레이아웃 앵커·필수 문구).

설계 §6(`docs/plans/2026-11-04-doctor-report-v4-design.md`). 필수 문구는 뷰 모델
(`DoctorReportV4.required_texts`)에서 뽑는다 — 이 파일에 문장을 손으로 복사하지 않는다.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from math import isfinite
from typing import Any

from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
from app.services.doctor_pdf_v3 import RenderedDocument
from app.services.doctor_report_v4 import TEMPLATE_VERSION, DoctorReportV4

BODY_PAGES = 6
MIN_PAGES = BODY_PAGES + 1
# 설계는 7~9쪽이지만 콘텐츠 20편·키워드 30개인 달이 보고서를 막지 않게 10쪽까지 둔다.
MAX_PAGES = 10
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


def v4_layout_valid(document: RenderedDocument, keyword_row_count: int) -> bool:
    """본문 6쪽은 쪽마다 한 장, 부록은 7쪽부터 이어진다. 앵커가 @page 여백 안에 있어야 한다.

    `p`로 시작하는 모든 앵커가 검사 대상이다 — 쪽 앵커 `p{n}-start/end`뿐 아니라 넓은 표의 맨 오른쪽
    칸에 둔 `p2-table-end`·`p3-heat-end`·`p4-table-end`·`p6-table-end`도 포함이라, 표가 오른쪽 레일을
    넘으면(2-up 히트맵의 긴 키워드 등) 그 앵커가 레일 밖에 놓여 거절된다.
    """
    if not MIN_PAGES <= len(document.pages) <= MAX_PAGES:
        return False
    for index in range(BODY_PAGES):
        anchors = document.pages[index].anchors
        if any(f"p{index + 1}-{edge}" not in anchors for edge in ("start", "end")):
            return False
    if "appendix-start" not in document.pages[BODY_PAGES].anchors:
        return False
    anchors = {name for page in document.pages for name in page.anchors}
    if "appendix-end" not in anchors:
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
                if not left <= x1 <= x2 <= right or not top <= y1 <= y2 <= bottom:
                    return False
    return True


def v4_expectation(
    view: DoctorReportV4, expectation: DoctorPdfExpectation, page_count: int
) -> DoctorPdfExpectation:
    texts = view.required_texts()
    return replace(
        expectation,
        expected_page_count=page_count,
        main_page_texts=texts[:BODY_PAGES],
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


__all__ = ["v4_expectation", "v4_layout_valid", "validate_v4_fields"]

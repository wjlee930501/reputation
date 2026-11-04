"""원장용 월간 보고서 v4 렌더 — 쪽 수·앵커·필수 문구·히트맵·금지어·의료광고 필터.

가상 병원·가상 키워드만 쓴다(병원명에 '검증용 가상'). WeasyPrint를 실제로 돌리므로 시나리오마다
한 번만 렌더하고(모듈 캐시) 결과를 여러 검사가 나눠 읽는다.
"""

from __future__ import annotations

import io
import re
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from types import SimpleNamespace
from uuid import UUID

import pytest
from pypdf import PdfReader
from test_doctor_report_view import BANNED_IN_DOCTOR_COPY

from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
from app.services.doctor_pdf_rendering import (
    mark_highlight,
    render_v4_html,
    render_validated_doctor_pdf,
)
from app.services.doctor_pdf_v4 import v4_expectation, v4_layout_valid, validate_v4_fields
from app.services.doctor_report_artifact import build_doctor_pdf_expectation
from app.services.doctor_report_v4 import DoctorReportV4, build_doctor_report_v4
from app.services.report_artifact_validation import INTERNAL_ONLY_MARKERS, internal_markers_in
from app.utils.medical_filter import check_forbidden

PUBLIC_URL = "https://fictional-clinic.example.invalid/"
WHEN = datetime(2026, 11, 1, 1, tzinfo=UTC)
HOSPITAL_ID = UUID("00000000-0000-4000-8000-000000000001")
CONTENT_ID = UUID("00000000-0000-4000-8000-000000000002")
TREATMENTS = ("위내시경", "대장내시경", "심장초음파", "고혈압", "당뇨", "건강검진")
INTENTS = ("추천 탐색", "증상 탐색", "비용 확인", "진료 가능 확인")
FORMS = ("잘하는 병원", "받을 수 있는 병원", "비용은 어느 정도인가요", "진료 가능한 병원")
COMPETITORS = ("검증용 가상 가온내과의원", "검증용 가상 바른속내과의원", "검증용 가상 한빛검진센터")
# 지시문 6-2·6-3의 옛 표현. '약속드리지 않'은 부록 D 밖에서만 금지다.
BANNED_IN_V4 = (
    "우리 병원", "언급 비율", "올린 글", "출처로 쓰인 답변", "다듬겠습니다", "원장님,",
    *(term for term in BANNED_IN_DOCTOR_COPY if term != "언급률"),
)
SCENARIOS = ("comparable", "first", "method-changed", "unavailable", "setup", "dense", "long-name")


def _view(scenario: str) -> DoctorReportV4:
    kind = "SETUP" if scenario == "setup" else "MONTHLY"
    setup = kind == "SETUP"
    dense = scenario == "dense"
    unavailable = scenario == "unavailable"
    reason = {"first": "NO_PRIOR_MANIFEST", "method-changed": "MEASUREMENT_POLICY_CHANGED"}.get(scenario)
    comparable = scenario in ("comparable", "dense", "long-name")
    name = "검증용 가상 새봄내과건강검진의원"
    if scenario == "long-name":
        name = ("검증용 가상 서울한마음가정의학과건강검진센터내과의원 " * 3)[:60]
        assert len(name) == 60
    hospital = SimpleNamespace(
        id=HOSPITAL_ID, name=name, slug="fictional-clinic",
        aeo_domain="fictional-clinic.example.invalid", region=["가상시 가상구"],
        hero_specialties=["위내시경"], treatments=[{"name": "심장초음파"}],
    )
    count = 30 if dense else 8
    keywords = [f"가상구 {TREATMENTS[i % 6]} {FORMS[i % 4]} {i + 1:02d}" for i in range(count)]
    targets = [
        SimpleNamespace(
            id=UUID(int=0x1000 + i), name=keywords[i], variants=[], condition_or_symptom=TREATMENTS[i % 6],
            treatment=None, specialty=None, target_intent=INTENTS[i % 4], region_terms=["가상구"],
        )
        for i in range(count)
    ]

    def cell(i: int, platform: str) -> tuple[str, int, int] | None:
        if unavailable:
            return ("FAILED", 0, 0)
        if i % 7 == 6 and platform == "gemini":
            return ("FAILED", 0, 0)
        if i == 5 and platform == "gemini":
            return None  # 셀 자체가 없는 키워드 → 히트맵 '측정 없음'
        mentioned = [3, 0, 1, 0, 2][i % 5] if platform == "chatgpt" else [2, 0, 0, 0, 1][i % 5]
        return ("SUCCESS", mentioned, 3)

    queries, cells, question_rows = [], [], []
    totals = {"chatgpt": [0, 0], "gemini": [0, 0]}
    for i, text in enumerate(keywords):
        attempts = mentioned = 0
        for platform in ("chatgpt", "gemini"):
            c = cell(i, platform)
            if c is None:
                continue
            cells.append({"query_text": text, "platform": platform, "state": c[0], "mentioned_attempts": c[1], "attempts_used": c[2]})
            attempts += c[2]
            mentioned += c[1]
            totals[platform][0] += c[1]
            totals[platform][1] += c[2]
        queries.append({"query_text": text, "query_intent": "LOCAL", "attempts_used": attempts, "mentioned_attempts": mentioned})
        question_rows.append({
            "query_text": text, "prior_measured": comparable, "prior_comparable": comparable,
            "prior_attempts_used": 6 if comparable else 0, "prior_mentioned_attempts": [1, 2, 0, 0, 1][i % 5] if comparable else 0,
            "current_attempts_used": attempts, "current_mentioned_attempts": mentioned,
        })
    total_attempts = sum(q["attempts_used"] for q in queries)
    total_mentioned = sum(q["mentioned_attempts"] for q in queries)
    sov = None if unavailable else round(total_mentioned / total_attempts * 100, 2)
    comparison = {
        "status": "COMPARABLE" if comparable else "NON_COMPARABLE",
        "reason": "NO_MATCHED_CELLS" if unavailable else (reason or "MATCHED_COHORT"),
        "matched_cell_count": count * 2 if comparable else 0,
        "current_sov_pct": sov, "prior_sov_pct": 31.0 if comparable else None,
    }
    coverage = {} if setup else {
        "sov_pct": sov, "attempts_used": total_attempts, "mentioned_attempts": total_mentioned,
        "comparison": comparison, "planned_count": count * 2,
        "success_count": sum(1 for c in cells if c["state"] == "SUCCESS"),
        "failed_count": sum(1 for c in cells if c["state"] == "FAILED"),
        "platforms": [
            {"platform": p, "mention_rate": (None if not a else round(m / a * 100, 2)), "attempts_used": a, "mentioned_attempts": m}
            for p, (m, a) in totals.items()
        ],
        "queries": queries, "cells": cells,
    }
    attribution = {
        "has_prior_month": comparable, "question_rows": question_rows,
        "new_mention_cells": [{"query_text": keywords[2], "platform_label": "ChatGPT"}] if comparable else [],
        "lost_mention_cells": [{"query_text": keywords[3], "platform_label": "Gemini"}] if comparable else [],
    }
    content_count = 20 if dense else 6
    contents = [
        SimpleNamespace(
            id=UUID(int=CONTENT_ID.int + i), hospital_id=HOSPITAL_ID,
            title=f"검증용 가상 {TREATMENTS[i % 6]} 안내 {i + 1:02d} — 준비와 확인 사항",
            status="PUBLISHED", content_type="FAQ", query_target_id=targets[i % count].id,
            first_published_at=datetime(2026, 10, 1 + i, 2, tzinfo=UTC),
            content_brief={"target_keyword": f"가상구 {TREATMENTS[i % 6]}"},
        )
        for i in range(content_count)
    ]
    citations = None if setup else {
        "measured_cell_count": 0 if unavailable else count * 2,
        "cited_cell_count": 0 if unavailable else 2, "cited_content_count": 0 if unavailable else 1,
        "cited_items": [] if unavailable else [{
            "content_id": str(contents[0].id), "title": contents[0].title, "cited_cell_count": 2,
            "queries": [{"query_text": keywords[0], "platform_label": "ChatGPT"}],
        }],
    }
    records = []
    if not setup and not unavailable:
        for i, text in enumerate(keywords):
            for platform in ("chatgpt", "gemini"):
                c = cell(i, platform)
                if c is None or c[0] != "SUCCESS":
                    continue
                mentioned = c[1] > 0
                records.append(SimpleNamespace(
                    is_mentioned=mentioned, measurement_status="SUCCESS", ai_platform=platform, measured_at=WHEN,
                    matched_text=name if mentioned else None,
                    raw_response=(
                        f"가상구에서 {TREATMENTS[i % 6]}을 받을 수 있는 곳으로 {name}이 있습니다. "
                        "예약 전에 검사 준비 사항과 진료 시간을 병원에 확인하는 것이 좋습니다."
                        if mentioned
                        else f"가상구에서 {TREATMENTS[i % 6]}을 받으려면 {COMPETITORS[i % 3]} 등을 살펴보실 수 있습니다. "
                        "진료 시간과 검사 준비 사항은 병원마다 다르니 방문 전에 확인하시기 바랍니다."
                    ),
                    competitor_mentions=[
                        {"name": COMPETITORS[(i + j) % 3], "is_mentioned": (i + (platform == "gemini")) % 3 == 0 and j == 0}
                        for j in range(3)
                    ],
                    query=SimpleNamespace(query_text=text),
                ))
    return build_doctor_report_v4(
        hospital=hospital, period_year=2026, period_month=10, public_url=PUBLIC_URL, report_kind=kind,
        sov_coverage=coverage, attribution=None if setup else attribution, citations=citations,
        published_contents=contents, query_targets=targets, records=records,
        plan_quota=None if setup else 16, published_count=content_count, fulfilled_count=content_count,
        cumulative_published_count=content_count + 28, comparison_reason=None if setup else comparison["reason"],
        reference_prev_sov_pct=31.0 if scenario == "method-changed" else None,
        v0_baseline={"of_hundred": 17, "current_of_hundred": round(sov or 0), "sentence": "참고"} if scenario == "method-changed" else None,
        trend_points=[("2026-08", 18.0), ("2026-09", 24.0)] if comparable else [],
        platforms=["chatgpt", "gemini"], sample_label="검증용 가상 사례",
    )


@lru_cache(maxsize=None)
def _rendered(scenario: str):
    view = _view(scenario)
    expectation = build_doctor_pdf_expectation(view, PUBLIC_URL)
    rendered = render_validated_doctor_pdf(
        view=view, period_label=view.period_ym, public_url=PUBLIC_URL, expectation=expectation
    )
    html = render_v4_html(view, period_label=view.period_ym, public_url=PUBLIC_URL)
    return view, rendered, html


def _squash(text: str) -> str:
    return "".join(text.split())


def _body_text(html: str) -> str:
    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_style))


def _lines(html: str) -> list[str]:
    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<(?:p|h1|h2|h3|td|th|tr|div|li|section|span)\b[^>]*>", "\n", without_style)
    text = re.sub(r"<[^>]+>", "", text)
    return [line.strip() for line in text.splitlines() if line.strip()]


# ── 렌더·검증 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_v4_renders_validates_and_binds_every_page(scenario):
    view, rendered, _ = _rendered(scenario)
    assert rendered.metadata.validation_version == "doctor-pdf-v4"
    assert 7 <= rendered.metadata.page_count <= 9
    assert rendered.metadata.font_family == "Pretendard"
    reader = PdfReader(io.BytesIO(rendered.pdf_bytes))
    pages = [_squash(page.extract_text() or "") for page in reader.pages]
    texts = view.required_texts()
    for index in range(6):
        for text in texts[index]:
            assert _squash(text) in pages[index], f"{scenario} p{index + 1}: {text!r}"
    appendix = "".join(pages[6:])
    for text in texts[6]:
        assert _squash(text) in appendix, f"{scenario} 부록: {text!r}"
    for page in pages:
        assert "검증용가상사례" in page
    assert not internal_markers_in("\n".join(pages))


def test_dense_month_continues_the_plan_into_the_appendix():
    view, rendered, html = _rendered("dense")
    assert len(view.appendix.keyword_rows) == 30 and len(view.content.rows) == 20
    assert len(view.plan.rows) == 8 and len(view.plan.continued) == 4
    assert rendered.metadata.page_count == 8
    body = _body_text(html)
    assert "다음 달 실행 계획 (이어짐)" in body
    assert "나머지 4개는 부록에 이어집니다." in body
    assert html.count("kw-row-29-end") == 1


def test_comparable_month_draws_trend_and_heatmap_states():
    view, _, html = _rendered("comparable")
    assert len(view.exposure.trend) == 3
    assert '<polyline' in html and html.count("<circle") == 3
    for state in ("mentioned", "unmentioned", "failed", "unmeasured"):
        assert f'class="heat heat-{state}"' in html, state
    assert "<mark>" in html
    assert "지난달 31%" in _body_text(html) and "<th class=\"num\">지난달</th>" in html


def test_first_month_hides_trend_and_previous_column():
    view, _, html = _rendered("first")
    assert view.exposure.trend == () and view.previous_rate is None
    assert "<polyline" not in html
    assert "<th class=\"num\">지난달</th>" not in html and "notice" not in _body_text(html)


def test_method_changed_month_keeps_prior_rate_in_appendix_only():
    view, _, html = _rendered("method-changed")
    assert view.summary.notice and view.summary.notice in _body_text(html)
    assert "지난달 AI 노출률 31%" in view.appendix.keyword_note
    assert "<th class=\"num\">지난달</th>" not in html
    body = _body_text(html)
    assert "지난달 31%" not in body
    assert view.appendix.keyword_note in body


def test_unavailable_and_setup_print_labels_never_zero():
    for scenario in ("unavailable", "setup"):
        view, _, html = _rendered(scenario)
        body = _body_text(html)
        for card in view.summary.cards[1:]:
            assert card.value in body
        assert "0%" not in body, scenario
        assert ("측정 미완료" if scenario == "unavailable" else "다음 달부터 측정") in body


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("banned", BANNED_IN_V4)
def test_v4_copy_avoids_retired_terms(scenario, banned):
    _, _, html = _rendered(scenario)
    assert banned not in _body_text(html), f"{scenario}: {banned!r}"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_promise_caveat_only_in_appendix_d(scenario):
    _, _, html = _rendered(scenario)
    before, _, after = html.partition('class="appendix-lines caveats"')
    assert "약속드리지 않" not in _body_text(before)
    assert "약속하지 않습니다" in _body_text(after)
    for marker in INTERNAL_ONLY_MARKERS:
        assert marker not in _body_text(html)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_v4_copy_passes_medical_ad_filter(scenario):
    _, _, html = _rendered(scenario)
    # 값 칸(26%·3/3·100%)은 문장이 아니라 숫자다 — 필터는 문장을 본다.
    lines = [line for line in _lines(html) if not re.fullmatch(r"[\d.,/%\s—]+", line)]
    assert lines
    assert [line for line in lines if check_forbidden(line)] == []


# ── 계약 단위 검사(WeasyPrint 없음) ──────────────────────────────────────────


# WeasyPrint 좌표는 CSS px — A4 793.7 × 1122.5px, 앵커 상자는 (x1, y1, x2, y2). @page 여백이
# 옆 17mm(64.25px)·머리 18mm(68.0px)·발 20mm(75.6px)라 본문 상자는 x 64.25~729.45, y 68.0~1046.9다.
A4_PX = (793.7, 1122.5)
RAIL = (64.25, 729.45)


def _document(anchor_pages: list[dict[str, tuple]]):
    return SimpleNamespace(
        pages=[SimpleNamespace(width=A4_PX[0], height=A4_PX[1], anchors=dict(page)) for page in anchor_pages]
    )


def _valid_pages(keyword_rows: int = 2) -> list[dict[str, tuple]]:
    pages = [
        {f"p{n}-start": (RAIL[0], 68.0, RAIL[1], 900.0), f"p{n}-end": (RAIL[0], 900.0, RAIL[1], 900.0)}
        for n in range(1, 7)
    ]
    appendix = {"appendix-start": (RAIL[0], 68.0, RAIL[1], 1000.0), "appendix-end": (RAIL[0], 1000.0, RAIL[1], 1000.0)}
    for index in range(keyword_rows):
        appendix[f"kw-row-{index}"] = (RAIL[0], 200.0 + 30 * index, RAIL[1], 230.0 + 30 * index)
        appendix[f"kw-row-{index}-end"] = (677.9, 205.0 + 30 * index, 677.9, 225.0 + 30 * index)
    return [*pages, appendix]


def test_layout_contract_requires_six_body_pages_then_appendix():
    assert v4_layout_valid(_document(_valid_pages()), 2)
    assert not v4_layout_valid(_document(_valid_pages()[:6]), 2)
    swapped = _valid_pages()
    swapped[2], swapped[3] = swapped[3], swapped[2]
    assert not v4_layout_valid(_document(swapped), 2)
    assert not v4_layout_valid(_document(_valid_pages(1)), 2)
    low = _valid_pages()
    low[0]["p1-end"] = (RAIL[0], 1060.0, RAIL[1], 1060.0)  # 발 여백(20mm) 안
    assert not v4_layout_valid(_document(low), 2)
    tall = _valid_pages()
    tall[6]["kw-row-1"] = (RAIL[0], 1030.0, RAIL[1], 1060.0)  # y1은 통과, y2가 발 여백을 넘는다
    assert not v4_layout_valid(_document(tall), 2)
    wide = _valid_pages()
    wide[6]["kw-row-0"] = (RAIL[0], 200.0, 760.0, 230.0)  # x2가 오른쪽 레일을 넘는다
    assert not v4_layout_valid(_document(wide), 2)
    high = _valid_pages()
    high[2]["p3-start"] = (RAIL[0], 40.0, RAIL[1], 900.0)  # 머리 여백(18mm) 안
    assert not v4_layout_valid(_document(high), 2)
    assert v4_layout_valid(_document([*_valid_pages()[:6], *([_valid_pages()[6]] * 4)]), 2)
    assert not v4_layout_valid(_document([*_valid_pages()[:6], *([_valid_pages()[6]] * 5)]), 2)
    spill = _valid_pages()
    spill[5].pop("p6-end")
    spill[6]["p6-end"] = (RAIL[0], 120.0, RAIL[1], 120.0)
    assert not v4_layout_valid(_document(spill), 2)


def test_layout_contract_rejects_table_end_anchors_past_the_rail():
    """넓은 표의 맨 오른쪽 칸 앵커(p2-table-end·p3-heat-end·p4-table-end·p6-table-end)도 레일 검사를 받는다."""
    pages = _valid_pages()
    pages[1]["p2-table-end"] = (700.0, 500.0, 700.0, 520.0)
    pages[2]["p3-heat-end"] = (720.0, 700.0, 720.0, 712.0)
    pages[3]["p4-table-end"] = (710.0, 300.0, 710.0, 320.0)
    pages[5]["p6-table-end"] = (715.0, 300.0, 715.0, 320.0)
    assert v4_layout_valid(_document(pages), 2)
    for page_index, name in ((1, "p2-table-end"), (2, "p3-heat-end"), (3, "p4-table-end"), (5, "p6-table-end")):
        wide = _valid_pages()
        wide[page_index][name] = (740.0, 500.0, 760.0, 520.0)  # 오른쪽 레일(729.45px) 밖
        assert not v4_layout_valid(_document(wide), 2), name


def _layout(html: str):
    from weasyprint import HTML

    from app.services.doctor_pdf_rendering import _TEMPLATE_DIR

    return HTML(string=html, base_url=str(_TEMPLATE_DIR)).render()


def _walk(box):
    yield box
    for child in getattr(box, "children", ()):
        yield from _walk(child)


def _element_boxes(document, tag: str, css_class: str):
    for page in document.pages:
        for box in _walk(page._page_box):
            element = getattr(box, "element", None)
            if element is not None and element.tag == tag and css_class in (element.get("class") or "").split():
                yield box


def test_rendered_pages_carry_table_end_anchors_inside_the_rail():
    for scenario in ("comparable", "dense"):
        view = _view(scenario)
        documents = [
            _layout(render_v4_html(view, period_label=view.period_ym, public_url=PUBLIC_URL, density=density))
            for density in ("comfortable", "compact")
        ]
        valid = [doc for doc in documents if v4_layout_valid(doc, len(view.appendix.keyword_rows))]
        assert valid, scenario  # 렌더러가 고르는 밀도(comfortable, 안 되면 compact) 중 하나는 통과한다
        names = {name for page in valid[0].pages for name in page.anchors}
        assert {"p2-table-end", "p3-heat-end", "p4-table-end", "p6-table-end"} <= names, scenario


def test_over_wide_heatmap_fails_the_layout_contract():
    """2-up 히트맵이 레일보다 넓어지면(긴 키워드·잘못된 열 너비) p3-heat-end가 레일 밖에 놓여 거절된다."""
    view = _view("dense")
    html = render_v4_html(view, period_label=view.period_ym, public_url=PUBLIC_URL, density="compact")
    assert v4_layout_valid(_layout(html), len(view.appendix.keyword_rows))
    forced = html.replace("</style>", ".v4 .heatmap { width:140% !important; }</style>", 1)
    assert not v4_layout_valid(_layout(forced), len(view.appendix.keyword_rows))


CARD_VALUE_SAMPLES = ("15개 중 15개", "15개 중 0개", "100.0%", "다음 달부터 측정", "측정 미완료", "12개 항목", "0개 항목", "20편")


@pytest.mark.parametrize("value", CARD_VALUE_SAMPLES)
def test_card_values_never_cross_into_the_next_card(value):
    """카드 큰 숫자는 글자 수로 크기를 정하고 마지막 수단으로 줄을 바꾸므로, 어떤 값도 옆 카드로 흘러들지 않는다."""
    view = _view("long-name")
    summary = replace(view.summary, cards=tuple(replace(card, value=value) for card in view.summary.cards))
    view = replace(view, summary=summary)
    for density in ("comfortable", "compact"):
        document = _layout(render_v4_html(view, period_label=view.period_ym, public_url=PUBLIC_URL, density=density))
        cells = [box for box in _element_boxes(document, "td", "card-cell") if type(box).__name__ == "TableCellBox"]
        assert len(cells) == 4, density
        cells.sort(key=lambda box: box.position_x)
        for index, cell in enumerate(cells):
            limit = cells[index + 1].position_x if index + 1 < len(cells) else RAIL[1] + 1.0
            lines = [line for line in _walk(cell) if type(line).__name__ == "LineBox"]
            assert lines, (density, index, value)  # 두 줄로 접혀도 되지만 옆 카드로는 흘러들지 않는다
            right = max(line.position_x + line.width for line in lines)
            assert right <= limit, (density, index, value, right, limit)


def _long_keyword_view() -> DoctorReportV4:
    """키워드 18개(2-up), 글자 40자짜리 긴 키워드 — 칸 안에서 줄을 바꿔야 한다."""
    view = _view("dense")
    rows = tuple(
        replace(row, keyword=(f"가상구에서 {TREATMENTS[i % 6]} 진료와 검사를 함께 받을 수 있는 병원 추천해줘 {i + 1:02d} 야간진료").ljust(40, "가")[:40])
        for i, row in enumerate(view.exposure.heatmap[:18])
    )
    assert all(len(row.keyword) == 40 for row in rows)
    return replace(view, exposure=replace(view.exposure, heatmap=rows))


@pytest.mark.parametrize("fixture, min_row_pt", (("dense", 16.0), ("long-keywords", 18.0)))
@pytest.mark.parametrize("density", ("comfortable", "compact"))
def test_heatmap_keywords_stay_inside_their_cells(fixture, min_row_pt, density):
    """키워드 글줄은 칸을 넘지 않고(x2 ≤ 칸 오른쪽), 행은 밀도별 최소 높이 이상이다(21개 이상 compact 16pt)."""
    view = _view("dense") if fixture == "dense" else _long_keyword_view()
    document = _layout(render_v4_html(view, period_label=view.period_ym, public_url=PUBLIC_URL, density=density))
    cells = [box for box in _element_boxes(document, "td", "heat-keyword") if type(box).__name__ == "TableCellBox"]
    assert len(cells) >= len(view.exposure.heatmap) // 2
    for cell in cells:
        lines = [line for line in _walk(cell) if type(line).__name__ == "LineBox"]
        for line in lines:
            assert line.position_x + line.width <= cell.position_x + cell.width + 0.5, (fixture, density)
    rows = [
        box for page in document.pages for box in _walk(page._page_box)
        if type(box).__name__ == "TableRowBox" and getattr(box, "element", None) is not None
        and any(getattr(child, "element", None) is not None and "heat-keyword" in (child.element.get("class") or "").split() for child in box.children)
    ]
    assert rows
    floor = (min_row_pt if (density == "compact" or fixture == "long-keywords") else 22.0) * 96 / 72
    if fixture == "dense" and density == "comfortable":
        floor = 22.0 * 96 / 72
    for row in rows:
        assert row.height >= floor - 0.5, (fixture, density, row.height, floor)


def test_v4_expectation_is_drawn_from_the_view_model():
    view = _view("comparable")
    base = DoctorPdfExpectation(view.hospital_name, "coverage", "caveat", PUBLIC_URL, appendix_expected=True)
    bound = v4_expectation(view, replace(base, required_appendix_texts=("기존",)), 7)
    assert bound.template_version == "v4"
    assert bound.expected_page_count == 7
    assert bound.main_page_texts == view.required_texts()[:6]
    assert bound.required_appendix_texts == ("기존", *view.required_texts()[6])
    assert bound.required_links == view.required_links() and len(bound.required_links) == 6
    assert bound.required_overview_texts == ()


def test_artifact_expectation_for_v4_uses_method_and_caveat_lines():
    view = _view("comparable")
    expectation = build_doctor_pdf_expectation(view, PUBLIC_URL)
    assert expectation.template_version == "v4"
    assert expectation.coverage_text == view.appendix.method_lines[0]
    assert expectation.caveat_text == "순위나 환자 수 증가를 약속하지 않습니다."
    assert expectation.appendix_expected is True


def test_field_validation_rejects_control_characters_and_bad_rates():
    view = _view("comparable")
    validate_v4_fields(view)
    broken = replace(view, summary=replace(view.summary, title="제목\x00"))
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_v4_fields(broken)
    assert caught.value.code == "DOCTOR_PDF_LAYOUT_LIMIT"
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_v4_fields(replace(view, current_rate=140.0))
    assert caught.value.code == "DOCTOR_PDF_METRIC_INVALID"


def test_pct_filter_matches_the_view_model_rule():
    from app.services.doctor_pdf_rendering import pct
    from app.services.doctor_report_v4 import _pct

    for value in (0.0, 33.3, 66.5, 99.4, 99.5, 100.0):
        assert pct(value) == _pct(value)
    assert pct(100.0) == "100.0%" and pct(26.4) == "26%"


def test_competitor_rows_print_count_label_and_optional_basis():
    view, _, html = _rendered("comparable")
    body = _body_text(html)
    assert view.competition.rows and view.competition.basis in body
    for row in view.competition.rows:
        assert row.count_label and row.count_label in body


def test_mark_highlight_escapes_and_marks_only_the_needle():
    assert str(mark_highlight("a <b> 병원 c", "병원")) == "a &lt;b&gt; <mark>병원</mark> c"
    assert str(mark_highlight("a <b>", "")) == "a &lt;b&gt;"
    assert str(mark_highlight("답변", "없음")) == "답변"


def test_v4_binary_validation_is_page_bound():
    """전체 문서에서 찾으면 통과하던 '내용이 1쪽에 몰린 PDF'를 쪽 단위 검사가 막는다."""
    from io import BytesIO

    from pypdf import PdfWriter

    from app.services.report_artifact_validation import validate_doctor_pdf

    view, rendered, _ = _rendered("comparable")
    reader = PdfReader(io.BytesIO(rendered.pdf_bytes))
    bound = v4_expectation(view, replace(build_doctor_pdf_expectation(view, PUBLIC_URL), period_label=view.period_ym), 7)
    assert validate_doctor_pdf(rendered.pdf_bytes, bound).validation_version == "doctor-pdf-v4"
    # 2쪽과 3쪽을 바꿔 끼운 PDF: 글자는 전부 있지만 쪽이 다르다.
    writer = PdfWriter()
    order = [0, 2, 1, 3, 4, 5, 6]
    for index in order:
        writer.add_page(reader.pages[index])
    swapped = BytesIO()
    writer.write(swapped)
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_doctor_pdf(swapped.getvalue(), bound)
    assert caught.value.code == "DOCTOR_PDF_REQUIRED_TEXT_MISSING" and "p2:" in caught.value.problem
    # 부록 문구가 본문 쪽에만 있는 PDF(부록 자리에 1쪽 사본): 전체 검색이면 통과했을 것이다.
    writer = PdfWriter()
    for index in range(6):
        writer.add_page(reader.pages[index])
    writer.add_page(reader.pages[0])
    moved = BytesIO()
    writer.write(moved)
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_doctor_pdf(moved.getvalue(), bound)
    assert "appendix:" in caught.value.problem


def test_v4_validation_rejects_single_page_with_all_text(monkeypatch):
    """모든 필수 문구가 1쪽에 다 있어도 v4는 쪽별 검사라 거절한다(v3 전역 검사와 다르다)."""
    from app.services import report_artifact_validation as module

    view, rendered, _ = _rendered("comparable")
    reader = PdfReader(io.BytesIO(rendered.pdf_bytes))
    everything = "\n".join(page.extract_text() or "" for page in reader.pages)
    first = reader.pages[0]

    class _Page:
        mediabox = first.mediabox

        def extract_text(self):
            return everything

        def get(self, key, default=None):
            return first.get(key, default)

    class _Reader:
        def __init__(self, *_args, **_kwargs):
            self.pages = [_Page()]

    monkeypatch.setattr(module, "PdfReader", _Reader)
    monkeypatch.setattr(module, "_uri_links", lambda page: tuple(view.required_links()) + (PUBLIC_URL,))
    bound = v4_expectation(view, replace(build_doctor_pdf_expectation(view, PUBLIC_URL), period_label=view.period_ym), 1)
    with pytest.raises(DoctorPdfValidationError) as caught:
        module.validate_doctor_pdf(rendered.pdf_bytes, bound)
    assert caught.value.code == "DOCTOR_PDF_REQUIRED_TEXT_MISSING"
    assert "p2:" in caught.value.problem and "appendix:" in caught.value.problem
    # 같은 1쪽 PDF라도 v3 계약(전역 검색)은 그대로 통과한다 — 옛 동작 불변.
    legacy = replace(bound, template_version=None)
    assert module.validate_doctor_pdf(rendered.pdf_bytes, legacy).validation_version == "doctor-pdf-v3"


def test_public_url_mismatch_fails_before_rendering():
    view = _view("comparable")
    expectation = build_doctor_pdf_expectation(view, PUBLIC_URL)
    with pytest.raises(DoctorPdfValidationError) as caught:
        render_validated_doctor_pdf(
            view=view, period_label=view.period_ym, public_url="https://other.example.invalid/",
            expectation=expectation,
        )
    assert caught.value.code == "DOCTOR_PDF_PUBLIC_URL_INVALID"

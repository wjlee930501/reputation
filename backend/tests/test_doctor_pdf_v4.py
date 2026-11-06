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
from test_doctor_report_v4 import MARKETING_REMOVED_COPY, with_legacy_copy
from test_doctor_report_view import BANNED_IN_DOCTOR_COPY

from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
from app.services.doctor_pdf_pagination import orphan_fragments
from app.services.doctor_pdf_rendering import (
    layout_v4,
    mark_highlight,
    render_v4_html,
    render_validated_doctor_pdf,
)
from app.services.doctor_pdf_v4 import (
    TABLE_ANCHORS,
    required_table_anchors,
    section_pages,
    v4_expectation,
    v4_layout_valid,
    validate_v4_fields,
)
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


def _view(scenario: str, *, count: int | None = None, content_count: int | None = None) -> DoctorReportV4:
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
    count = count or (30 if dense else 8)
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
    content_count = content_count or (20 if dense else 6)
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


def _validated_layout(view: DoctorReportV4):
    """렌더러와 같은 선택: comfortable이 앵커 검사를 통과하면 그것, 아니면 compact(쪽 넘김 정리 포함)."""
    for density in ("comfortable", "compact"):
        document, _ = layout_v4(view, period_label=view.period_ym, public_url=PUBLIC_URL, density=density)
        if v4_layout_valid(document, len(view.appendix.keyword_rows), required_anchors=required_table_anchors(view)):
            return document, density
    raise AssertionError("no density validates")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_v4_renders_validates_and_binds_every_page(scenario):
    """절은 새 쪽에서 시작하고 길면 흐른다 — 절의 문구는 그 절의 쪽 범위 안에, 부록 문구는 부록 쪽에만 있다."""
    view, rendered, _ = _rendered(scenario)
    assert rendered.metadata.validation_version == "doctor-pdf-v4"
    assert 7 <= rendered.metadata.page_count <= 14
    assert rendered.metadata.font_family == "Pretendard"
    document, _ = _validated_layout(view)
    assert len(document.pages) == rendered.metadata.page_count
    sections = section_pages(document)
    assert sections is not None
    reader = PdfReader(io.BytesIO(rendered.pdf_bytes))
    pages = [_squash(page.extract_text() or "") for page in reader.pages]
    texts = view.required_texts()
    for index, (start, end) in enumerate(sections.body):
        section_text = "".join(pages[start : end + 1])
        for text in texts[index]:
            assert _squash(text) in section_text, f"{scenario} p{index + 1}: {text!r}"
    appendix = "".join(pages[sections.appendix_start :])
    for text in texts[6]:
        assert _squash(text) in appendix, f"{scenario} 부록: {text!r}"
    for page in pages:
        assert "검증용가상사례" in page
    assert not internal_markers_in("\n".join(pages))


def test_dense_month_continues_the_plan_into_the_appendix():
    view, rendered, html = _rendered("dense")
    assert len(view.appendix.keyword_rows) == 30 and len(view.content.rows) == 20
    assert len(view.plan.rows) == 8 and len(view.plan.continued) == 4
    assert 9 <= rendered.metadata.page_count <= 12  # 읽기 최소치를 지키며 02·03·04·부록이 흐른다
    body = _body_text(html)
    assert "다음 달 실행 계획 (이어짐)" in body
    assert "나머지 4개는 부록에 이어집니다." in body
    assert html.count("kw-row-29-end") == 1


def test_comparable_month_draws_trend_and_heatmap_states():
    view, _, html = _rendered("comparable")
    assert len(view.exposure.trend) == 3
    assert '<polyline' in html and html.count("<circle") == 3
    # 측정 실패 칸은 '측정 없음'과 같은 칸으로 그린다 — 원장용 보고서에 '측정 실패' 빗금을 두지 않는다(2026-11-06).
    for state in ("mentioned", "unmentioned", "unmeasured"):
        assert f'class="heat heat-{state}"' in html, state
    assert 'class="heat heat-failed"' not in html and "측정 실패" not in _body_text(html)
    assert "<mark>" in html
    assert "지난달 31%" in _body_text(html) and "<th class=\"num\">지난달</th>" in html


def test_first_month_hides_trend_and_previous_column():
    view, _, html = _rendered("first")
    assert view.exposure.trend == () and view.previous_rate is None
    assert "<polyline" not in html
    assert "<th class=\"num\">지난달</th>" not in html and "notice" not in _body_text(html)


def test_method_changed_month_keeps_prior_rate_in_appendix_only():
    view, _, html = _rendered("method-changed")
    # 1면 '직접 비교하지 않았습니다 … 부록 A에 있습니다' 안내 줄은 두지 않는다(2026-11-06) — 참고치는 부록 A에만 있다.
    assert view.summary.notice == "" and "직접 비교하지" not in _body_text(html)
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
def test_marketing_removed_copy_is_absent_and_appendix_b_d_are_gone(scenario):
    """2026-11-06 마케팅팀 요청 — 부정적·단정적·어색한 공통 문구 9개 예시가 어느 보고서에도 찍히지 않는다."""
    _, _, html = _rendered(scenario)
    body = "".join(_body_text(html).split())
    for phrase in MARKETING_REMOVED_COPY:
        assert "".join(phrase.split()) not in body, phrase
    for heading in ("B. 측정 방법", "D. 유의사항"):
        assert heading not in html
    assert 'class="appendix-lines caveats"' not in html
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
    assert v4_layout_valid(_document([*_valid_pages()[:6], *([_valid_pages()[6]] * 8)]), 2)  # 14쪽
    assert not v4_layout_valid(_document([*_valid_pages()[:6], *([_valid_pages()[6]] * 9)]), 2)  # 15쪽


def test_layout_contract_lets_sections_flow_in_order():
    """절은 새 쪽에서 시작해 여러 쪽에 걸칠 수 있다 — start는 오름차순, end는 start 이상·다음 start 미만, 부록은 p6-end 뒤."""
    flowing = _valid_pages()
    flowing[2] = {"p3-start": (RAIL[0], 68.0, RAIL[1], 1000.0)}  # 3절이 3·4쪽에 걸친다
    flowing.insert(3, {"p3-start": (RAIL[0], 68.0, RAIL[1], 400.0), "p3-end": (RAIL[0], 400.0, RAIL[1], 400.0)})
    assert v4_layout_valid(_document(flowing), 2)
    assert section_pages(_document(flowing)).body == ((0, 0), (1, 1), (2, 3), (4, 4), (5, 5), (6, 6))
    not_first = _valid_pages()
    not_first.insert(0, {"appendix-start": (RAIL[0], 68.0, RAIL[1], 900.0)})
    assert not v4_layout_valid(_document(not_first), 2)  # p1이 0쪽이 아니다
    shared = _valid_pages()
    shared[1]["p3-start"] = (RAIL[0], 950.0, RAIL[1], 1000.0)  # 3절이 2절의 끝 쪽에서 시작한다(새 쪽이 아니다)
    shared[2].pop("p3-start")
    assert not v4_layout_valid(_document(shared), 2)
    spill = _valid_pages()
    spill[5].pop("p6-end")
    spill[6]["p6-end"] = (RAIL[0], 120.0, RAIL[1], 120.0)  # 6절의 끝이 부록 첫 쪽에 있다
    assert not v4_layout_valid(_document(spill), 2)
    backwards = _valid_pages()
    backwards[3]["p4-end"] = (RAIL[0], 900.0, RAIL[1], 900.0)
    backwards[2]["p4-end"] = (RAIL[0], 900.0, RAIL[1], 900.0)  # end가 start보다 앞 쪽에도 있다 → 첫 발견이 앞 쪽
    backwards[3].pop("p4-end")
    assert not v4_layout_valid(_document(backwards), 2)


def test_layout_contract_rejects_wide_or_missing_table_anchors():
    """넓은 표의 앵커 상자(표 자체의 사각형)는 가로 레일 검사를 받고, 뷰가 요구한 앵커가 빠지면 거절한다."""
    pages = _valid_pages()
    placement = {"p2-table": 1, "p3-heat": 2, "p4-table": 3, "p6-table": 5, "appendix-table": 6, "appendix-plan-table": 6}
    for name, page_index in placement.items():
        pages[page_index][name] = (RAIL[0], 300.0, RAIL[1], 1100.0)  # 세로는 쪽을 넘어 이어질 수 있다
    assert v4_layout_valid(_document(pages), 2, required_anchors=frozenset(TABLE_ANCHORS))
    for name, page_index in placement.items():
        wide = [dict(page) for page in pages]
        wide[page_index][name] = (RAIL[0], 300.0, 749.4, 900.0)  # 103% 너비: 오른쪽 레일(729.45px) 밖
        assert not v4_layout_valid(_document(wide), 2), name
        shifted = [dict(page) for page in pages]
        shifted[page_index][name] = (RAIL[0], 1300.0, RAIL[1], 1900.0)  # 쪽 아래로 1200px 옮겨진 표
        assert not v4_layout_valid(_document(shifted), 2), name
        missing = [dict(page) for page in pages]
        missing[page_index].pop(name)
        assert v4_layout_valid(_document(missing), 2)  # 요구하지 않으면 선택
        assert not v4_layout_valid(_document(missing), 2, required_anchors=frozenset({name})), name


def test_layout_contract_keeps_appendix_anchors_in_the_appendix():
    """appendix-end는 appendix-start 쪽 이상, p* 앵커는 부록 뒤에 없고, kw-row 앵커는 부록 쪽에만 있다."""
    early_end = _valid_pages()
    early_end[6].pop("appendix-end")
    early_end[5]["appendix-end"] = (RAIL[0], 950.0, RAIL[1], 950.0)  # 부록 시작 전에 끝 앵커
    assert not v4_layout_valid(_document(early_end), 2)
    body_row = _valid_pages()
    body_row[6].pop("kw-row-1")
    body_row[6].pop("kw-row-1-end")
    body_row[3]["kw-row-1"] = (RAIL[0], 200.0, RAIL[1], 230.0)  # 본문 쪽의 키워드 행
    body_row[3]["kw-row-1-end"] = (677.9, 205.0, 677.9, 225.0)
    assert not v4_layout_valid(_document(body_row), 2)
    late_section = _valid_pages()
    late_section.append({"p3-heat": (RAIL[0], 200.0, RAIL[1], 400.0), "appendix-end": (RAIL[0], 500.0, RAIL[1], 500.0)})
    late_section[6].pop("appendix-end")
    assert not v4_layout_valid(_document(late_section), 2)  # 부록 뒤 쪽의 p* 앵커


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
        required = required_table_anchors(view)
        assert required <= frozenset(TABLE_ANCHORS), scenario
        assert ("appendix-plan-table" in required) == bool(view.plan.continued), scenario
        valid = [doc for doc in documents if v4_layout_valid(doc, len(view.appendix.keyword_rows), required_anchors=required)]
        assert valid, scenario  # 렌더러가 고르는 밀도(comfortable, 안 되면 compact) 중 하나는 통과한다
        names = {name for page in valid[0].pages for name in page.anchors}
        assert required <= names, scenario


@pytest.mark.parametrize(
    "selector, name",
    (
        (".v4 .heatmap", "p3-heat"),
        (".v4 .content-table", "p2-table"),
        (".v4 .keyword-table", "appendix-table"),
        (".v4 .appendix .plan-table", "appendix-plan-table"),
    ),
)
def test_over_wide_table_fails_the_layout_contract(selector, name):
    """표가 레일보다 조금(103%)만 넓어져도 표 앵커의 사각형이 레일 밖으로 나가 거절된다."""
    view = _view("dense")
    rows = len(view.appendix.keyword_rows)
    html = render_v4_html(view, period_label=view.period_ym, public_url=PUBLIC_URL, density="comfortable")
    required = required_table_anchors(view)
    assert v4_layout_valid(_layout(html), rows, required_anchors=required)
    forced = html.replace("</style>", f"{selector} {{ width:103% !important; }}</style>", 1)
    document = _layout(forced)
    rect = next(page.anchors[name] for page in document.pages if name in page.anchors)
    assert rect[2] > RAIL[1] + 1.0
    assert not v4_layout_valid(document, rows, required_anchors=required)


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


@pytest.mark.parametrize("fixture", ("dense", "long-keywords"))
@pytest.mark.parametrize("density", ("comfortable", "compact"))
def test_heatmap_keywords_stay_inside_their_cells(fixture, density):
    """키워드 글줄은 칸을 넘지 않고(x2 ≤ 칸 오른쪽), 행은 밀도와 무관하게 22pt 이상이다."""
    view = _view("dense") if fixture == "dense" else _long_keyword_view()
    document, _ = layout_v4(view, period_label=view.period_ym, public_url=PUBLIC_URL, density=density)
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
    floor = 22.0 * 96 / 72
    for row in rows:
        assert row.height >= floor - 0.5, (fixture, density, row.height, floor)


MIN_BAR_ROW_PT = 30.0
MIN_TABLE_ROW_PT = 22.0
MIN_CLEARANCE_PT = 4.0


def _readability_rows(document):
    """P2~P6·부록의 막대 행(.bar-row)·원장 행(.rule-row)·표 본문 행(tbody tr)을 (종류, 행 상자) 로 모은다."""
    for page_index, page in enumerate(document.pages):
        if page_index == 0:
            continue
        for box in _walk(page._page_box):
            element = getattr(box, "element", None)
            if element is None:
                continue
            classes = (element.get("class") or "").split()
            if type(box).__name__ == "TableRowBox" and element.tag == "tr":
                cells = [child for child in box.children if getattr(child, "element", None) is not None]
                if any(child.element.tag == "th" for child in cells):
                    continue  # 머리 행(thead)은 본문 행 최소치의 대상이 아니다
                yield ("bar" if "bar-row" in classes else "row"), box
            elif element.tag == "div" and "rule-row" in classes and type(box).__name__ == "BlockBox":
                yield "row", box


def _cell_lines(box):
    name = type(box).__name__
    if name == "LineBox":
        yield box
        return
    if name == "InlineBlockBox":
        return
    for child in getattr(box, "children", ()):
        yield from _cell_lines(child)


@pytest.mark.parametrize("fixture", ("growth", "dense", "long-keywords"))
@pytest.mark.parametrize("density", ("comfortable", "compact"))
def test_readability_minimums(fixture, density):
    """읽기 최소치(2026-11 대표 확정): 막대 행 30pt·표/원장 행 22pt 이상, 글줄은 헤어라인에서 4pt 이상 떨어지고
    한 행 안의 글줄끼리 겹치지 않는다 — 밀도(compact)도 이 값을 줄이지 못한다."""
    view = _long_keyword_view() if fixture == "long-keywords" else _view("comparable" if fixture == "growth" else "dense")
    document, _ = layout_v4(view, period_label=view.period_ym, public_url=PUBLIC_URL, density=density)
    px = 96 / 72
    rows = list(_readability_rows(document))
    assert len(rows) > 20
    for kind, row in rows:
        floor = (MIN_BAR_ROW_PT if kind == "bar" else MIN_TABLE_ROW_PT) * px
        assert row.height >= floor - 0.5, (fixture, density, kind, row.height / px)
        top, bottom = row.position_y, row.position_y + row.height
        for cell in (child for child in row.children if type(child).__name__ == "TableCellBox"):
            # 칸 안의 블록(막대 라벨 + 캡션 블록)을 끝까지 내려가 글줄을 모은다. inline-block(막대 트랙·값 칸)의
            # 안쪽 글줄은 바깥 글줄과 같은 자리에 겹쳐 서므로 그 요소 안은 내려가지 않는다.
            lines = sorted(_cell_lines(cell), key=lambda line: line.position_y)
            for line in lines:
                assert line.position_y >= top + MIN_CLEARANCE_PT * px - 0.5, (fixture, density, kind)
                assert line.position_y + line.height <= bottom - MIN_CLEARANCE_PT * px + 0.5, (fixture, density, kind)
            for first, second in zip(lines, lines[1:], strict=False):
                assert second.position_y >= first.position_y + first.height - 0.5, (fixture, density, kind)


def _page_has(page, predicate) -> bool:
    return any(predicate(getattr(box, "element", None), box) for box in _walk(page._page_box))


ORPHAN_SWEEP = [(30, c) for c in range(8, 22)] + [(k, 20) for k in range(11, 31, 2)]


@pytest.mark.parametrize(("count", "content_count"), ORPHAN_SWEEP)
def test_tables_never_leave_orphan_rows(count, content_count):
    """쪽 넘김 고아·과부(2026-11-05): 흐르는 표의 조각은 어느 쪽에서도 3행 이상이고, 검정 면·시사점은 표 뒤에
    혼자 새 쪽에 서지 않는다(앞 행을 끌고 간다). 키워드 수·콘텐츠 수를 훑어 쪽 경계가 표의 여러 자리에 떨어지게 한다."""
    view = _view("dense", count=count, content_count=content_count)
    document, _ = _validated_layout(view)
    assert orphan_fragments(document) == {}, (count, content_count, orphan_fragments(document))
    # 검정 면·시사점이 쪽의 유일한 본문이면 안 된다(절의 첫 쪽은 제목이 함께 있다).
    for page in document.pages:
        has_band = _page_has(page, lambda el, _b: el is not None and el.tag == "div" and ({"figure-band", "insight-panel"} & set((el.get("class") or "").split())))
        if not has_band:
            continue
        has_body = _page_has(page, lambda el, b: (el is not None and el.tag in ("h1", "h2", "p") and not ({"running-head", "figure-label", "figure-value", "caption", "panel-label", "insight", "insight-label", "insight-text"} & set((el.get("class") or "").split()))) or type(b).__name__ == "TableRowBox")
        assert has_body, (count, content_count, "band alone on a page")


def _wide_keyword_dense_view(width: int = 48) -> DoctorReportV4:
    """Codex 13차 반례: 30개 키워드를 48자로 늘리면 정리 전 레이아웃이 14쪽 상한에 딱 닿는다."""
    view = _view("dense")
    long_keyword = lambda text: (text + " 검사 준비 사항과 진료 시간 안내 문의")[:width].ljust(width, "가")  # noqa: E731
    exposure = replace(view.exposure, heatmap=tuple(replace(row, keyword=long_keyword(row.keyword)) for row in view.exposure.heatmap))
    appendix = replace(view.appendix, keyword_rows=tuple(replace(row, keyword=long_keyword(row.keyword)) for row in view.appendix.keyword_rows))
    return replace(view, exposure=exposure, appendix=appendix)


def test_pagination_fixups_never_reject_a_previously_valid_layout():
    """고아 정리가 쪽 상한(14)을 넘기면 정리 없는 레이아웃으로 물러선다 — 정리 전에 통과하던 입력은 계속 통과한다."""
    view = _wide_keyword_dense_view()
    rendered = render_validated_doctor_pdf(
        view=view, period_label=view.period_ym, public_url=PUBLIC_URL,
        expectation=build_doctor_pdf_expectation(view, PUBLIC_URL),
    )
    assert rendered.metadata.validation_version == "doctor-pdf-v4"
    assert rendered.metadata.page_count <= 14


def test_pagination_fixups_fall_back_when_binary_validation_rejects_the_fixed_layout():
    """Codex 14차 반례: 계획 키워드 두 개를 300자로 늘리면 정리가 p6 모듈을 넘겨 제목만 남은 쪽(7자)을 만든다 —
    이진 검증(DOCTOR_PDF_PAGE_EMPTY)이 그 변형을 거절하면 정리 없는 변형으로 물러서 전처럼 통과한다."""
    view = _view("dense")
    rows = list(view.plan.rows)
    rows[0] = replace(rows[0], keyword="가" * 300)
    rows[1] = replace(rows[1], keyword="가" * 300)
    view = replace(view, plan=replace(view.plan, rows=tuple(rows)))
    rendered = render_validated_doctor_pdf(
        view=view, period_label=view.period_ym, public_url=PUBLIC_URL,
        expectation=build_doctor_pdf_expectation(view, PUBLIC_URL),
    )
    assert rendered.metadata.validation_version == "doctor-pdf-v4"
    assert rendered.metadata.page_count <= 14


def test_pagination_fixups_leave_the_appendix_start_alone():
    """부록 표의 첫 조각은 고치지 않는다(마스트헤드만 남은 빈 쪽을 만들지 않는다) — 600자 키워드 4행 스트레스."""
    view = _view("comparable")
    rows = tuple(replace(row, keyword=(row.keyword + " ") * 60) for row in view.appendix.keyword_rows[:4])
    view = replace(view, appendix=replace(view.appendix, keyword_rows=rows))
    document, breaks = layout_v4(view, period_label=view.period_ym, public_url=PUBLIC_URL)
    assert "appendix-table" not in breaks.before_table
    for page in document.pages:
        text = "".join(getattr(box, "text", "") for box in _walk(page._page_box))
        assert len(text.strip()) > 40, "masthead-only page"


def test_v4_expectation_is_drawn_from_the_view_model():
    view = _view("comparable")
    base = DoctorPdfExpectation(view.hospital_name, "coverage", "caveat", PUBLIC_URL, appendix_expected=True)
    # 쪽 범위 없이 부르면(옛 호출) 범위를 비워 두고 이진 검증이 '절 n = 쪽 n'으로 묶는다.
    bound = v4_expectation(view, replace(base, required_appendix_texts=("기존",)), 99)
    assert bound.template_version == "v4"
    assert bound.expected_page_count == 99
    assert bound.main_page_texts == view.required_texts()[:6]
    assert bound.section_page_ranges == ()
    # 레이아웃 증명의 절 쪽 범위(본문 6 + 부록)를 그대로 싣는다 — 3절(히트맵)은 두 쪽에 걸친다.
    document, _ = _validated_layout(view)
    sections = section_pages(document)
    flowing = v4_expectation(view, base, len(document.pages), sections)
    assert flowing.main_page_texts == view.required_texts()[:6]
    assert flowing.section_page_ranges == sections.ranges() and len(flowing.section_page_ranges) == 7
    assert flowing.section_page_ranges[6] == (sections.appendix_start, sections.appendix_end)
    assert sections.appendix_end == len(document.pages) - 1
    assert any(end > start for start, end in sections.body)
    assert bound.required_appendix_texts == ("기존", *view.required_texts()[6])
    assert bound.required_links == view.required_links() and len(bound.required_links) == 6
    assert bound.required_overview_texts == ()


def test_artifact_expectation_for_v4_uses_method_and_caveat_lines():
    # 부록 B·D가 없는 지금 판은 고정 문구를 요구하지 않는다(빈 문구 = 언제나 통과).
    view = _view("comparable")
    expectation = build_doctor_pdf_expectation(view, PUBLIC_URL)
    assert expectation.template_version == "v4"
    assert (expectation.coverage_text, expectation.caveat_text) == ("", "")
    assert expectation.appendix_expected is True
    # 옛 문구를 얼린 판(10월분)은 종전처럼 부록 B 첫 줄·부록 D 둘째 줄을 요구한다.
    legacy = with_legacy_copy(view)
    expectation = build_doctor_pdf_expectation(legacy, PUBLIC_URL)
    assert expectation.coverage_text == legacy.appendix.method_lines[0]
    assert expectation.caveat_text == "순위나 환자 수 증가를 약속하지 않습니다."


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
    document, _ = _validated_layout(view)
    sections = section_pages(document)
    bound = v4_expectation(view, replace(build_doctor_pdf_expectation(view, PUBLIC_URL), period_label=view.period_ym), len(document.pages), sections)
    assert validate_doctor_pdf(rendered.pdf_bytes, bound).validation_version == "doctor-pdf-v4"
    # 2절 쪽과 3절 첫 쪽을 바꿔 끼운 PDF: 글자는 전부 있지만 쪽이 다르다.
    writer = PdfWriter()
    order = list(range(len(reader.pages)))
    order[1], order[2] = order[2], order[1]
    for index in order:
        writer.add_page(reader.pages[index])
    swapped = BytesIO()
    writer.write(swapped)
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_doctor_pdf(swapped.getvalue(), bound)
    assert caught.value.code == "DOCTOR_PDF_REQUIRED_TEXT_MISSING" and "p2:" in caught.value.problem
    # 부록 문구가 본문 쪽에만 있는 PDF(부록 자리에 1쪽 사본): 전체 검색이면 통과했을 것이다.
    writer = PdfWriter()
    for index in range(sections.appendix_start):
        writer.add_page(reader.pages[index])
    for _ in range(sections.appendix_start, len(reader.pages)):
        writer.add_page(reader.pages[0])  # 쪽 수는 그대로(범위 검증을 통과해 문구 검증까지 간다)
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


def test_blank_continuation_page_is_rejected(monkeypatch):
    """절의 쪽 범위 안에 글자 없는 쪽이 끼면(빈 이어짐 쪽) DOCTOR_PDF_PAGE_EMPTY다."""
    from app.services import report_artifact_validation as module

    view, rendered, _ = _rendered("comparable")
    reader = PdfReader(io.BytesIO(rendered.pdf_bytes))
    document, _ = _validated_layout(view)
    sections = section_pages(document)
    texts = [page.extract_text() or "" for page in reader.pages]
    blank = sections.body[2][1]  # 3절의 이어짐 쪽
    assert blank > sections.body[2][0]
    texts[blank] = "Re:putation / 2026-10   03 / AI 노출 성과 (이어짐) · 검증용 가상 사례 · 4 / 9\nNEWVISIT · RE:PUTATION MOTIONLABS INC."
    first = reader.pages[0]

    class _Page:
        mediabox = first.mediabox

        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

        def get(self, key, default=None):
            return first.get(key, default)

    class _Reader:
        def __init__(self, *_args, **_kwargs):
            self.pages = [_Page(text) for text in texts]

    monkeypatch.setattr(module, "PdfReader", _Reader)
    monkeypatch.setattr(module, "_uri_links", lambda page: tuple(view.required_links()) + (PUBLIC_URL,))
    bound = v4_expectation(view, replace(build_doctor_pdf_expectation(view, PUBLIC_URL), period_label=view.period_ym), len(texts), sections)
    with pytest.raises(DoctorPdfValidationError) as caught:
        module.validate_doctor_pdf(rendered.pdf_bytes, bound)
    assert caught.value.code == "DOCTOR_PDF_PAGE_EMPTY" and f"{blank + 1}쪽" in caught.value.problem


def test_public_url_mismatch_fails_before_rendering():
    view = _view("comparable")
    expectation = build_doctor_pdf_expectation(view, PUBLIC_URL)
    with pytest.raises(DoctorPdfValidationError) as caught:
        render_validated_doctor_pdf(
            view=view, period_label=view.period_ym, public_url="https://other.example.invalid/",
            expectation=expectation,
        )
    assert caught.value.code == "DOCTOR_PDF_PUBLIC_URL_INVALID"


def test_layout_contract_closes_the_appendix_boundary_holes():
    """뒤에 남는 빈 쪽, 부록 첫 쪽의 p* 앵커, 부록 끝 뒤의 kw-row 앵커는 모두 거절한다(검토 11)."""
    trailing_blank = _valid_pages()
    trailing_blank.append({})  # 부록 끝 뒤에 아무 앵커도 없는 쪽
    assert not v4_layout_valid(_document(trailing_blank), 2)
    heat_in_appendix = _valid_pages()
    heat_in_appendix[6]["p3-heat"] = (RAIL[0], 300.0, RAIL[1], 420.0)  # 부록 첫 쪽에 본문 표
    assert not v4_layout_valid(_document(heat_in_appendix), 2)
    row_after_end = _valid_pages()
    row_after_end.append({"kw-row-1": (RAIL[0], 200.0, RAIL[1], 230.0), "kw-row-1-end": (677.9, 205.0, 677.9, 225.0)})
    row_after_end[6].pop("kw-row-1")
    row_after_end[6].pop("kw-row-1-end")
    assert not v4_layout_valid(_document(row_after_end), 2)


def test_binary_validation_rejects_malformed_section_ranges():
    """넘겨받은 쪽 범위는 본문 6 + 부록, 오름차순·겹침 없음·쪽 안·부록 끝 = 마지막 쪽이어야 한다."""
    from dataclasses import replace as _replace

    from app.services.report_artifact_validation import validate_doctor_pdf

    view, rendered, _ = _rendered("comparable")
    page_count = rendered.metadata.page_count
    document, _ = _validated_layout(view)
    good = v4_expectation(view, build_doctor_pdf_expectation(view, PUBLIC_URL), page_count, section_pages(document))
    assert validate_doctor_pdf(rendered.pdf_bytes, good).validation_version == "doctor-pdf-v4"
    overlapping = tuple((0, page_count - 1) for _ in range(7))
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_doctor_pdf(rendered.pdf_bytes, _replace(good, section_page_ranges=overlapping))
    assert caught.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"
    short_appendix = (*good.section_page_ranges[:-1], (good.section_page_ranges[-1][0], page_count - 2))
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_doctor_pdf(rendered.pdf_bytes, _replace(good, section_page_ranges=short_appendix))
    assert caught.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"
    five_body = _replace(good, main_page_texts=good.main_page_texts[:5], section_page_ranges=good.section_page_ranges[:6])
    with pytest.raises(DoctorPdfValidationError) as caught:
        validate_doctor_pdf(rendered.pdf_bytes, five_body)
    assert caught.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"

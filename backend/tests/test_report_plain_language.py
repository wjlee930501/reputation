"""원장용 월간 PDF의 쉬운 말 계약 — 가상 기록만 쓴다.

원장님은 마케터가 아니다. '비교 보류', '공통 눈금', '확정 관측' 같은 말은 읽히지 않는다.
대신 무엇이 좋아졌는지는 실제 숫자로만 말하고, 비교할 수 없는 달은 이유를 한 문장으로 쓴다.
"""

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from test_doctor_report_view import BANNED_IN_DOCTOR_COPY
from test_report_redesign import monthly_view

from app.utils.medical_filter import check_forbidden

# 9월 보고서에서 원장님이 이해하지 못한 표현과, 같은 결의 내부 용어.
BANNED_IN_MONTHLY_PDF = [
    *BANNED_IN_DOCTOR_COPY,
    "비교 보류", "공통 눈금", "이번 결과로 정할 일", "다음 비교의 기준",
    "확정 관측", "확정 반복", "판정 보류", "조합", "소유 URL", "관측", "수행",
    "약정", "기준선", "코호트", "DELIVERED", "OBSERVED", "APPENDIX", "baseline",
    "OpenAI API", "Google Gemini API", "챗GPT", "제미나이", "비교 불가",
    # 대표 지시(2026-10): '소개' 대신 '언급', '출발점' 대신 '기준점', 결손 나열 대신 다음 수.
    "소개", "출발점", "빠진 질문부터", "줄었습니다", "빠졌", "나오지 않았습니다",
    "내부 검수용", "원장 전달 불가", "토킹 포인트", "AE 전용",
]


def _coverage(current, prior, *, reason="MATCHED_COHORT"):
    comparable = reason == "MATCHED_COHORT" and current is not None
    return {
        "planned_count": 6,
        "success_count": 0 if current is None else 6,
        "attempts_used": 0 if current is None else 18,
        "mentioned_attempts": 0 if current is None else round((current or 0) * 18 / 100),
        "measurement_basis": {"cell_count": 6},
        "platforms": [
            {"platform": platform, "mention_rate": current, "attempts_used": 9,
             "mentioned_attempts": 3, "planned_count": 3, "success_count": 3,
             "failed_count": 0, "excluded_count": 0, "answer_models": ["fictional"]}
            for platform in ("chatgpt", "gemini")
        ],
        "observation_adequacy": {"lineage": "SLOTTED", "planned_slots": 18,
                                 "confirmed_slots": 18, "status": "COMPLETE"},
        "comparison": {
            "status": "COMPARABLE" if comparable else "NON_COMPARABLE",
            "reason": reason,
            "matched_cell_count": 6 if comparable else 0,
            "current_sov_pct": current,
            "prior_sov_pct": prior if comparable else None,
            "current_attempts_used": 18,
            "current_mentioned_attempts": round((current or 0) * 18 / 100),
        },
        "ci95_low": None if current is None else max(0.0, current - 10),
        "ci95_high": None if current is None else min(100.0, current + 10),
    }


def _attribution(*, prior: bool):
    return {
        "has_prior_month": prior,
        "new_mention_count": 1 if prior else 0,
        "new_mention_cells": [{"query_text": "가상동 검진 상담 병원", "platform_label": "ChatGPT"}] if prior else [],
        "lost_mention_cells": [{"query_text": "가상동 혈압 상담 병원", "platform_label": "Gemini"}] if prior else [],
        "first_measured_mention_count": 0 if prior else 1,
        "non_comparable_count": 0 if prior else 2,
        "question_rows": [
            {"query_text": "가상동 검진 상담 병원", "prior_measured": prior, "prior_comparable": prior,
             "prior_attempts_used": 6, "prior_mentioned_attempts": 0,
             "current_attempts_used": 6, "current_mentioned_attempts": 3},
            {"query_text": "가상동 혈압 상담 병원", "prior_measured": prior, "prior_comparable": prior,
             "prior_attempts_used": 6, "prior_mentioned_attempts": 2,
             "current_attempts_used": 6, "current_mentioned_attempts": 0},
        ],
    }


CITATIONS = {
    "measured_cell_count": 4, "cited_cell_count": 1, "cited_content_count": 1,
    "cited_items": [{"content_id": "fictional", "title": "가상 검진 안내", "cited_cell_count": 1,
                     "queries": [{"query_text": "가상동 검진 상담 병원", "platform_label": "ChatGPT"}]}],
}

SCENARIOS = {
    "up": dict(sov_pct=50.0, prev_sov_pct=30.0, comparison_reason="MATCHED_COHORT",
               sov_coverage=_coverage(50.0, 30.0), attribution=_attribution(prior=True)),
    "down": dict(sov_pct=10.0, prev_sov_pct=30.0, comparison_reason="MATCHED_COHORT",
                 sov_coverage=_coverage(10.0, 30.0), attribution=_attribution(prior=True)),
    "first": dict(sov_pct=40.0, prev_sov_pct=None, comparison_reason="NO_PRIOR_MANIFEST",
                  sov_coverage=_coverage(40.0, None, reason="NO_PRIOR_MANIFEST"),
                  attribution=_attribution(prior=False)),
    "method": dict(sov_pct=40.0, prev_sov_pct=None, comparison_reason="MEASUREMENT_POLICY_CHANGED",
                   sov_coverage=_coverage(40.0, None, reason="MEASUREMENT_POLICY_CHANGED"),
                   attribution=_attribution(prior=True)),
    # 측정 방식이 바뀐 달이지만 원장님이 지난달 받은 수치가 있다(몇 달째 관리해 온 병원).
    "method_reference": dict(sov_pct=50.3, prev_sov_pct=None, comparison_reason="MEASUREMENT_POLICY_CHANGED",
                             sov_coverage=_coverage(50.3, None, reason="MEASUREMENT_POLICY_CHANGED"),
                             attribution=_attribution(prior=True), reference_prev_sov_pct=40.0),
    "unavailable": dict(sov_pct=None, prev_sov_pct=30.0, comparison_reason="NO_MATCHED_CELLS",
                        sov_coverage=_coverage(None, 30.0, reason="NO_MATCHED_CELLS"),
                        attribution=None, citations={"measured_cell_count": 0, "cited_items": []}),
    "initial": dict(sov_pct=40.0, prev_sov_pct=None, comparison_reason="NO_PRIOR_MANIFEST",
                    report_kind="INITIAL", attribution=_attribution(prior=False)),
}


def _view(name: str):
    values = {"citations": CITATIONS, "platforms": ["chatgpt", "gemini"],
              "v0_baseline": {"of_hundred": 20, "current_of_hundred": 40, "sentence": "참고"},
              **SCENARIOS[name]}
    return monthly_view(**values)


def _html(view) -> str:
    templates = Path(__file__).parents[1] / "app/templates"
    environment = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape(("html",)))
    return environment.get_template("doctor_report_v3.html").render(
        view=view, period_label="2026-08", public_url="https://fictional.example.invalid/"
    )


def _body_text(html: str) -> str:
    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_style))


def _page(html: str, number: int) -> str:
    end = f'id="main-{number + 1}-start"' if number < 3 else 'id="appendix-start"'
    return _body_text(html.split(f'id="main-{number}-start"')[1].split(end)[0])


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("banned", BANNED_IN_MONTHLY_PDF)
def test_director_pdf_has_no_jargon_or_internal_markers(name, banned):
    assert banned not in _body_text(_html(_view(name))), f"{name}: '{banned}'"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_director_pdf_passes_the_medical_ad_filter(name):
    view = _view(name)
    narrative = view["narrative"]
    lines = [
        narrative.title, narrative.conclusion, narrative.comparison_note, narrative.denominator,
        narrative.citation_scope, narrative.fulfillment_note, *narrative.priorities,
        *narrative.methods, *narrative.platform_details, *narrative.citation_details,
        view["coverage_text"], view["new_mention_empty_text"], *view["footnotes"],
        *(str(value) for value in view["tiles"][0].values()),
        *(work.citation_label for work in narrative.works),
        *(work.appendix_label for work in narrative.works),
    ]
    assert [line for line in lines if check_forbidden(line)] == []
    assert check_forbidden(_body_text(_html(view))) == []


@pytest.mark.parametrize(
    "name,headline,prior_cell",
    [
        ("up", "AI 답변이 지난달보다 우리 병원을 더 자주 언급했습니다.", "30.0%"),
        ("down", "다음 달에는 더 넓은 키워드로 AI 답변 속 언급을 다시 늘려 가겠습니다.", "30.0%"),
        ("first", "첫 측정 결과입니다. 앞으로 이 숫자와 견주며 변화를 살피겠습니다.", "첫 측정"),
        ("method", "이번 결과를 새 기준점으로 두고, 다음 달부터 흐름을 짚어 드리겠습니다.", "비교 없음"),
        ("method_reference", "이번 달부터 측정 방식이 바뀌어, 지난달 수치는 참고용으로만 함께 적었습니다. 다음 달부터는 같은 방식으로 비교해 드리겠습니다.", "40.0%"),
        ("unavailable", "측정을 다시 진행한 뒤, 결과가 확인되는 대로 바로 알려 드리겠습니다.", "비교 없음"),
        ("initial", "첫 측정 결과입니다. 앞으로 이 숫자와 견주며 변화를 살피겠습니다.", "첫 측정"),
    ],
)
def test_first_page_leads_with_a_plain_headline_per_branch(name, headline, prior_cell):
    view = _view(name)
    page = _page(_html(view), 1)
    assert view["narrative"].conclusion == headline
    assert headline in page
    assert prior_cell in page
    assert "AI에게 물었을 때 우리 병원이 언급된 비율" in page
    assert "약속드리지는 않습니다" in page


@pytest.mark.parametrize(
    "name,reason",
    [
        ("first", "이번이 첫 측정이라 견줄 지난달 숫자는 아직 없습니다."),
        ("method", "측정 방식이 바뀌어서 지난달 결과와 직접 비교하지는 않았습니다."),
    ],
)
def test_a_month_that_was_not_compared_says_why_in_one_sentence(name, reason):
    page = _page(_html(_view(name)), 1)
    assert reason in page
    assert "%p" not in page


def test_every_noncomparable_reason_has_its_own_plain_sentence():
    reasons = ("NO_PRIOR_MANIFEST", "ANSWER_MODEL_CHANGED", "MEASUREMENT_POLICY_CHANGED",
               "QUERY_TEXT_CHANGED", "PLATFORM_COHORT_MISSING", "INTENT_SNAPSHOT_MISSING",
               "NO_MATCHED_CELLS", "ANSWER_MODEL_UNKNOWN", "SAMPLE_SHAPE_CHANGED")
    notes = {monthly_view(comparison_reason=reason)["narrative"].comparison_note for reason in reasons}
    assert len(notes) == len(reasons)


def test_comparable_month_explains_the_number_in_everyday_words():
    view = _view("up")
    page = _page(_html(view), 1)
    assert "100번 물으면 약 50번 우리 병원이 언급된 셈입니다." in page
    assert "지난달과 같은 질문을 ChatGPT·Gemini에 총 18회 물었고, 9회의 답변에 우리 병원이 언급됐습니다." in page
    assert "환자 수가 아닌 AI 답변 횟수 기준입니다." in page


def test_a_lower_month_leads_with_our_plan_while_the_numbers_stay_visible():
    view = _view("down")
    page = _page(_html(view), 1)
    assert view["narrative"].conclusion == "다음 달에는 더 넓은 키워드로 AI 답변 속 언급을 다시 늘려 가겠습니다."
    assert "30.0%" in page and "10.0%" in page
    assert "더 자주" not in page


def test_lost_and_never_mentioned_questions_become_next_month_actions():
    priorities = _view("down")["narrative"].priorities
    assert priorities[0] == (
        "“가상동 혈압 상담 병원”: Gemini 답변에 다시 언급되도록 관련 진료 안내 글을 보강합니다."
    )
    first = _view("first")["narrative"].priorities
    assert first[0] == "“가상동 혈압 상담 병원”: 이 질문에 바로 답하는 진료 안내 글을 준비합니다."


def test_repeated_next_steps_do_not_repeat_the_same_sentence():
    rows = [
        {"query_text": f"가상동 질문 {index}", "current_attempts_used": 6,
         "current_mentioned_attempts": 0}
        for index in range(3)
    ]
    priorities = monthly_view(attribution={"question_rows": rows})["narrative"].priorities
    endings = {line.split("”: ", 1)[1] for line in priorities}
    assert len(endings) == 3


def test_good_news_tiles_come_from_real_counts_and_keep_unknown_apart_from_zero():
    measured = _view("up")["highlights"]
    assert measured["measured_questions"] == 2
    assert measured["mentioned_questions"] == 1
    assert measured["cited_questions"] == 1
    assert measured["cumulative_published"] is None
    unavailable = _view("unavailable")["highlights"]
    assert unavailable["measured_questions"] is None
    assert unavailable["cited_questions"] is None
    zero = monthly_view(citations={"measured_cell_count": 4, "cited_cell_count": 0, "cited_items": []})
    assert zero["highlights"]["cited_questions"] == 0
    page = _page(_html(_view("unavailable")), 1)
    assert "확인 못 함" in page and "0번이라는 뜻은 아닙니다" in page


def test_cumulative_published_count_is_shown_only_when_known():
    view = monthly_view(cumulative_published_count=40)
    assert "지금까지 올린 글은 모두 40편입니다." in _page(_html(view), 1)
    assert "지금까지 올린 글은" not in _page(_html(monthly_view()), 1)


def test_contract_tile_uses_plain_words():
    view = monthly_view(published_count=13, plan_quota=12, supplementary_count=2)
    tile = view["tiles"][0]
    assert tile["label"] == "약속한 글 발행"
    assert tile["value"] == "12편 중 11편"
    assert tile["hint"] == "이번 달 실제로 올린 글 13편 · 이전 달 몫을 채운 글 2편 포함."
    assert view["narrative"].fulfillment_note == "남은 1편도 검수가 끝나는 대로 올려 드리겠습니다."


def test_page_markers_are_plain_korean():
    html = _html(_view("up"))
    for marker in ("이번 달 결과", "이번 달 한 일", "다음 달 계획", "숫자를 읽는 법", "자세한 기록"):
        assert marker in html


def test_template_page_markers_are_not_runtime_delivery_gates():
    from app.services import report_artifact_validation

    source = Path(report_artifact_validation.__file__).read_text()
    assert '("01 / 이번 달 결과", "02 / 이번 달 한 일", "03 / 다음 달 계획")' not in source


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_branch_renders_and_passes_the_director_pdf_validator(name):
    from io import BytesIO

    from pypdf import PdfReader

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.report_artifact_validation import internal_markers_in

    view = _view(name)
    expected = DoctorPdfExpectation(
        view["hospital_name"], view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/", appendix_expected=bool(view["appendix_rows"]),
    )
    rendered = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
    )
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(rendered.pdf_bytes)).pages)
    assert internal_markers_in(text) == []
    assert "".join(view["narrative"].conclusion.split()) in "".join(text.split())


def test_a_method_change_month_shows_last_month_as_reference_not_as_a_trend():
    """몇 달째 관리해 온 병원에 '기준점'이라 말하지 않되, 측정 방식 변경을 증감으로 팔지 않는다."""
    view = _view("method_reference")
    narrative = view["narrative"]
    page = _page(_html(view), 1)

    assert narrative.previous is None  # 비교 값이 아니다 — 증감 문장·검증은 이것만 본다
    assert narrative.reference_previous == 40.0
    assert "지난달(참고)" in page and "40.0%" in page and "50.3%" in page
    assert narrative.conclusion in page
    assert "지난달 수치는 참고용으로만" in page
    assert "기준점" not in page
    for trend in ("더 자주 언급됐습니다", "줄었", "늘었"):
        assert trend not in narrative.conclusion


def test_a_first_month_never_shows_a_reference_value():
    view = monthly_view(
        sov_pct=40.0, prev_sov_pct=None, comparison_reason="NO_PRIOR_MANIFEST",
        sov_coverage=_coverage(40.0, None, reason="NO_PRIOR_MANIFEST"),
        attribution=_attribution(prior=False), reference_prev_sov_pct=30.0,
    )
    assert view["narrative"].reference_previous is None
    assert view["narrative"].conclusion == "첫 측정 결과입니다. 앞으로 이 숫자와 견주며 변화를 살피겠습니다."

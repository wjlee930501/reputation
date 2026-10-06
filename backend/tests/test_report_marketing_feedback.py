"""마케팅팀 피드백으로 바뀐 원장용 월간 PDF 문구·계산 계약 — 가상 기록만 쓴다."""

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from test_report_plain_language import (
    SCENARIOS,
    _attribution,
    _body_text,
    _coverage,
    _html,
    _page,
    _view,
)
from test_report_redesign import monthly_view

from app.services.monthly_template_refresh import compare_doctor_pdf_facts
from app.services.report_narrative import PRIORITY_APPENDIX_LEAD

# 피드백에서 지운 표현. '그 결과'는 글과 답변의 인과를 암시하므로 어디에도 쓰지 않는다.
BANNED_PHRASES = [
    "곁에", "없는 병원과 같", "보시는 것이 안전", "새로 언급된 질문 계산", "묻기로 한",
    "실제 확인일", "위 순서대로", "AI가 언급한 질문", "AI가 참고한 우리 글",
    "그 결과", "일정대로 꾸준히", "글로 다듬어", "계속 다듬", "하나로 단정", "삼았다", "‘6번 중 2번’",
]


def _row(text, *, key=None, asked=6, found=0, prior=False):
    row = {
        "query_text": text, "prior_measured": prior, "prior_comparable": prior,
        "prior_attempts_used": 6 if prior else 0, "prior_mentioned_attempts": 0,
        "current_attempts_used": asked, "current_mentioned_attempts": found,
    }
    if key is not None:
        row["query_key"] = key
    return row


def _with_rows(rows, **extra):
    return monthly_view(attribution={"question_rows": rows}, **extra)


def _all_views():
    views = {name: _view(name) for name in SCENARIOS}
    views["many_unmentioned"] = _with_rows(
        [_row(f"가상동 질문 {i}") for i in range(5)] + [_row("가상동 언급된 질문", found=3)]
    )
    return views


@pytest.mark.parametrize("name", sorted(_all_views()))
def test_priority_lines_have_no_old_frame(name):
    for line in _all_views()[name]["narrative"].priorities:
        assert "처럼" not in line and "질문에서도" not in line


@pytest.mark.parametrize("name", sorted(_all_views()))
def test_feedback_banned_phrases_never_render(name):
    text = _body_text(_html(_all_views()[name]))
    for phrase in BANNED_PHRASES:
        assert phrase not in text, f"{name}: '{phrase}'"


def test_question_mentioned_under_one_query_key_is_not_listed_as_unmentioned():
    rows = [
        _row("가상동 중복 질문", key="a", found=0),
        _row("가상동 중복 질문", key="b", found=2),
        _row("가상동 다른 질문", key="c", found=0),
    ]
    view = _with_rows(rows)
    priorities = view["narrative"].priorities
    assert not any("가상동 중복 질문" in line for line in priorities)
    assert sum("가상동 다른 질문" in line for line in priorities) == 1


def test_same_text_unmentioned_under_two_keys_appears_once_and_caption_counts_texts():
    rows = [_row("가상동 중복 질문", key="a"), _row("가상동 중복 질문", key="b"),
            _row("가상동 다른 질문", key="c"), _row("가상동 언급된 질문", key="d", found=3)]
    view = _with_rows(rows)
    priorities = view["narrative"].priorities
    assert sum("가상동 중복 질문" in line for line in priorities) == 1
    assert len(priorities) == 2
    page3 = _page(_html(view), 3)
    assert "아직 언급되지 않은 질문 2개는 모두 다음 달 계획에 넣었습니다." in page3
    assert "한 가지" not in page3 and "두 가지를 먼저" in page3


def test_page_three_points_to_appendix_only_when_more_than_three_priorities():
    few = _page(_html(_with_rows([_row(f"가상동 질문 {i}") for i in range(3)])), 3)
    assert "세 가지를 먼저" in few and "뒤쪽 부록에 있습니다. 담당" not in few
    assert "위 세 가지 밖의 질문은 뒤쪽 부록에 있습니다." not in few
    many_view = _with_rows([_row(f"가상동 질문 {i}") for i in range(5)])
    many_html = _html(many_view)
    assert "아직 언급되지 않은 질문 5개는 모두 다음 달 계획에 넣었습니다. 위 세 가지 밖의 질문은 뒤쪽 부록에 있습니다." in _page(many_html, 3)
    appendix = _body_text(many_html.split('id="appendix-start"')[1])
    assert "그 밖에 살펴볼 질문" in appendix and PRIORITY_APPENDIX_LEAD in appendix


def test_reference_month_says_it_once_on_page_one_and_short_note_in_appendix():
    view = _view("method_reference")
    html = _html(view)
    sentence = "이번 달부터 측정 방식이 바뀌어, 지난달 수치는 참고용으로만 함께 적었습니다."
    assert _body_text(html).count(sentence) == 1
    assert sentence in _page(html, 1)
    assert view["narrative"].comparison_note == "이번 달부터 측정 방식이 바뀌어 지난달 수치는 참고용으로만 적었습니다."
    appendix = _body_text(html.split('id="appendix-start"')[1])
    assert view["narrative"].comparison_note in appendix
    assert view["narrative"].comparison_note not in _page(html, 1)


def test_other_noncomparable_reason_marks_last_month_as_reference_only():
    view = monthly_view(
        sov_pct=50.0, prev_sov_pct=None, comparison_reason="ANSWER_MODEL_CHANGED",
        sov_coverage=_coverage(50.0, None, reason="ANSWER_MODEL_CHANGED"),
        attribution=_attribution(prior=True), reference_prev_sov_pct=40.0,
    )
    narrative = view["narrative"]
    assert narrative.conclusion == "지난달과 같은 조건으로 비교할 수 없어, 지난달 수치는 참고용으로만 함께 적었습니다."
    assert narrative.comparison_note.endswith("지난달 수치는 참고용으로만 함께 적었습니다.")


def test_appendix_example_comes_from_a_real_row():
    view = _view("up")
    assert view["appendix_example"] == (
        "‘6번 중 3번’은 ChatGPT·Gemini에 합쳐 6번 물어 우리 병원이 3번 언급됐다는 뜻입니다."
    )
    rows = [_row("가상동 질문 A", asked=10, found=4), _row("가상동 질문 B", asked=6)]
    custom = _with_rows(rows, platforms=["chatgpt", "gemini"])
    assert custom["appendix_example"].startswith("‘10번 중 4번’은 ChatGPT·Gemini에 합쳐 10번 물어")
    assert custom["appendix_example"] in _body_text(_html(custom))


def test_appendix_example_is_absent_when_no_row_has_a_mention():
    view = _with_rows([_row("가상동 질문 A"), _row("가상동 질문 B")])
    assert view["appendix_example"] is None
    assert "합쳐" not in _body_text(_html(view).split('id="appendix-start"')[1]).split("질문 지난달")[0]


def test_appendix_basis_sentence_follows_comparability():
    comparable = _body_text(_html(_view("up")).split('id="appendix-start"')[1])
    first = _body_text(_html(_view("first")).split('id="appendix-start"')[1])
    assert "첫 장의 지난달 비교는 두 달 모두 같은 방식으로 물어본 질문만으로 계산했습니다." in comparable
    assert "첫 장의 비율은 이번 달에 물어본 질문 전체로 계산했습니다." not in comparable
    assert "첫 장의 비율은 이번 달에 물어본 질문 전체로 계산했습니다." in first
    assert "첫 장의 지난달 비교는" not in first


def _coverage_text(adequacy, records=()):
    coverage = _coverage(40.0, None, reason="NO_PRIOR_MANIFEST")
    coverage["observation_adequacy"] = adequacy
    return monthly_view(
        sov_coverage=coverage, platforms=["chatgpt", "gemini"], records=list(records)
    )["coverage_text"]


def test_coverage_text_all_confirmed_says_every_answer_was_checked():
    text = _coverage_text({"lineage": "SLOTTED", "planned_slots": 18, "confirmed_slots": 18, "status": "COMPLETE"})
    assert "같은 질문을 반복해 물은 18회 전부 답을 확인했습니다." in text
    assert "측정 범위: ChatGPT, Gemini에서 받기로 한 답변 6건 중 6건을 확인했습니다." in text
    assert "확인하지 못한 답이 있으면 ‘언급되지 않음’으로 세지 않습니다." in text


def test_coverage_text_comparable_month_names_matched_answers():
    coverage = _coverage(50.0, 30.0)
    text = monthly_view(sov_coverage=coverage, platforms=["chatgpt", "gemini"],
                        comparison_reason="MATCHED_COHORT", prev_sov_pct=30.0)["coverage_text"]
    assert "첫 장의 지난달 비교는 두 달 모두 같은 질문으로 받은 답변 6건으로 계산했습니다." in text


def test_coverage_text_partial_names_confirmed_out_of_planned():
    text = _coverage_text({"lineage": "SLOTTED", "planned_slots": 18, "confirmed_slots": 15, "status": "PARTIAL"})
    assert "일부만 확인한 달입니다." in text
    assert "18회 가운데 15회의 답을 확인했습니다." in text
    assert "전부" not in text


def test_coverage_text_uses_single_date_or_range_without_old_label():
    adequacy = {"lineage": "SLOTTED", "planned_slots": 18, "confirmed_slots": 18, "status": "COMPLETE"}

    def day(d):
        return SimpleNamespace(measured_at=datetime(2026, 8, d, 3, tzinfo=timezone.utc))

    same = _coverage_text(adequacy, [day(5), day(5)])
    assert same.endswith("확인일: 2026-08-05.")
    ranged = _coverage_text(adequacy, [day(5), day(9)])
    assert ranged.endswith("확인일: 2026-08-05 ~ 2026-08-09.")
    assert "실제 확인일" not in same + ranged


def test_stored_fact_only_in_old_pdf_is_not_a_difference():
    old = "지난달 33.3% 이번 달 66.7% 같은 질문을 반복해 150번 중 150번 답을 확인"
    new = "지난달 33.3% 이번 달 66.7% 같은 질문을 반복해 물은 150회 전부 답을 확인"
    assert compare_doctor_pdf_facts(old, new, stored_facts=frozenset({"150번중150번"})) == []


def test_old_only_fact_not_in_stored_facts_still_fails():
    old = "지난달 33.3% 이번 달 66.7% 약속한 글 12편 중 12편 150번 중 150번"
    new = "지난달 33.3% 이번 달 66.7% 150회 전부"
    assert compare_doctor_pdf_facts(old, new, stored_facts=frozenset({"150번중150번"})) == [
        "옛 PDF에만 있음: 12편중12편"
    ]


def test_many_unmentioned_questions_render_a_valid_director_pdf():
    from pypdf import PdfReader

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf

    view = _all_views()["many_unmentioned"]
    assert len(view["narrative"].priorities) == 5
    expected = DoctorPdfExpectation(
        view["hospital_name"], view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/", appendix_expected=bool(view["appendix_rows"]),
    )
    rendered = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
    )
    text = "".join("".join(page.extract_text().split()) for page in PdfReader(BytesIO(rendered.pdf_bytes)).pages)
    assert "".join(
        "아직 언급되지 않은 질문 5개는 모두 다음 달 계획에 넣었습니다.".split()
    ) in text
    assert "".join(PRIORITY_APPENDIX_LEAD.split()) in text


def test_range_footnote_keeps_percent_on_both_ends_for_template_refresh_parity():
    """옛 PDF의 '16.7% ~ 30.0%'와 새 문구가 같은 숫자 사실(16.7%·30.0%)을 담아야 템플릿 갱신이 통과한다."""
    from app.services.report_engine import _director_footnotes

    notes = _director_footnotes(
        {"ci95_low": 16.7, "ci95_high": 30.0},
        names="ChatGPT, Gemini",
        first_measured_questions=0,
        non_comparable_questions=0,
        has_v0_baseline=False,
    )
    line = next(note for note in notes if "범위로 보시면 됩니다" in note)
    assert "16.7%~30.0%" in line
    old = "이번 달 비율은 대략 16.7% ~ 30.0% 사이로 보시는 것이 안전합니다."
    assert compare_doctor_pdf_facts(old, line) == []


def test_stored_range_bounds_count_as_stored_facts_for_template_refresh():
    """'8.2~20.0%'로 찍힌 옛 버전을 '8.2%~20.0%'로 다시 찍어도 저장값 그대로면 차이가 아니다."""
    from app.services.monthly_template_refresh import stored_pdf_fact_tokens

    summary = {"ci95_low": 8.2, "ci95_high": 20.0}
    facts = stored_pdf_fact_tokens(summary)
    assert {"8.2%", "20.0%"} <= facts
    old = "이번 달 비율은 대략 8.2~20.0% 범위로 보시면 됩니다."
    new = "이번 달 비율은 대략 8.2%~20.0% 범위로 보시면 됩니다."
    assert compare_doctor_pdf_facts(old, new, stored_facts=facts) == []
    # 저장값과 다른 숫자는 여전히 차이다.
    assert compare_doctor_pdf_facts(old, new.replace("8.2%", "9.2%"), stored_facts=facts)

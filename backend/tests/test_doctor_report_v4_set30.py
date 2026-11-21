"""원장 보고서 v4 — 측정 키워드 30개 세트(설계 §7).

- 1면 AI 노출률·노출 키워드 수·진료 항목은 병원명 질문(A)을 넣지 않는다(B~D만).
- P3 그룹 표 A행은 실제 값이고, 그 아래 병원 정보 정확도 한 줄이 붙는다.
- 그룹은 측정표에 얼린 값(payload)에서 읽는다 — 저장값만으로 같은 사실을 다시 만든다.
- 30개 세트 달에는 '첫 장은 지난달과 같은 키워드 기준' 안내 줄을 싣지 않는다(측정 방식 설명 금지).
- 옛 15개 달(그룹·BRAND 없음)은 화면·사실이 그대로다.
"""

from __future__ import annotations

import copy

from test_doctor_report_v4 import (
    MARKETING_REMOVED_COPY,
    PLATFORMS,
    PUBLIC_URL,
    _coverage,
    _stored,
    build,
)

from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.doctor_report_artifact import build_doctor_pdf_expectation
from app.services.doctor_report_v4 import compare_facts_v4, stored_facts_v4

BRAND = {
    "가상동 튼튼내과의원 어때?": (5, 6),
    "가상동 튼튼내과의원 진료 시간 알려줘": (6, 6),
}
LOCAL_GROUPS = {
    "가상동 심장초음파 잘하는 병원": ("B", "FIXED"),
    "가상동 위내시경 가능한 병원": ("B", "ROTATING"),
    "가슴이 답답할 때 가상동 병원": ("C", "FIXED"),
    "가상동 건강검진 비용": ("D", "FIXED"),
}
ACCURACY = {
    "version": 1,
    "answers": 12,
    "fields": {
        "address": {"label": "주소", "match": 4, "mismatch": 1, "absent": 7, "checked": 5, "mismatch_excerpts": []},
        "hours": {"label": "진료 시간", "match": 0, "mismatch": 0, "absent": 12, "checked": 0, "mismatch_excerpts": []},
        "treatments": {"label": "진료 항목", "match": 3, "mismatch": 0, "absent": 9, "checked": 3, "mismatch_excerpts": []},
    },
}


def _set30_coverage(*, unmatched: int = 2) -> dict:
    """monthly_sov가 저장하는 모양: 대표 노출률·플랫폼은 LOCAL만, queries·cells에는 BRAND도 있다."""

    coverage = _coverage(unmatched=unmatched)
    for query in coverage["queries"]:
        group, role = LOCAL_GROUPS[query["query_text"]]
        query["question_group"], query["set_role"] = group, role
    for cell in coverage["cells"]:
        group, role = LOCAL_GROUPS[cell["query_text"]]
        cell["question_group"], cell["set_role"] = group, role
    for index, (text, (mentioned, attempts)) in enumerate(BRAND.items()):
        key = f"brand{index}"
        coverage["queries"].append({
            "query_key": key, "query_text": text, "query_intent": "BRAND",
            "attempts_used": attempts, "mentioned_attempts": mentioned, "measured_count": 2,
            "mentioned_count": 2, "question_group": "A", "set_role": "FIXED",
        })
        for platform in PLATFORMS:
            coverage["cells"].append({
                "query_key": key, "query_text": text, "query_intent_label": "병원명 질문",
                "platform": platform, "state": "SUCCESS", "attempts_used": attempts // 2,
                "mentioned_attempts": mentioned // 2 if platform == "gemini" else mentioned - mentioned // 2,
                "question_group": "A", "set_role": "FIXED",
            })
    coverage["brand_accuracy"] = copy.deepcopy(ACCURACY)
    return coverage


def test_brand_questions_stay_out_of_the_headline_cards():
    legacy = build()
    view = build(sov_coverage=_set30_coverage())

    facts = view.facts()
    # 대표 노출률·노출 키워드 수는 옛 4개 키워드(B~D)와 같다 — A 2개는 세지 않는다.
    assert facts["card.rate"] == legacy.facts()["card.rate"]
    assert facts["card.keywords"] == legacy.facts()["card.keywords"] == "4개 중 2개"
    assert not any("튼튼내과의원 어때" in name for name in facts if name.startswith("treatment."))


def test_group_table_fills_a_from_brand_answers_and_adds_the_accuracy_line():
    view = build(sov_coverage=_set30_coverage())

    rows = {row.group: row for row in view.exposure.group_rows}
    assert rows["A"].keyword_count == 2
    assert rows["A"].rate == 91.67
    assert rows["A"].detail == "AI 답변 12건 중 11건"
    # 그룹은 payload에서 — 위내시경(타깃 의도는 가능 확인 → B)도 B, 비용 질문은 D.
    assert rows["D"].keyword_count == 1
    assert view.exposure.group_note == "AI가 병원 정보를 맞게 안내한 답변: 주소 5건 중 4건, 진료 항목 3건 중 3건"
    heat = {row.keyword for row in view.exposure.heatmap}
    assert set(BRAND) <= heat
    appendix = {row.keyword: row for row in view.appendix.keyword_rows}
    assert appendix["가상동 튼튼내과의원 어때?"].group == "A"
    assert appendix["가상동 건강검진 비용"].group == "D"


def test_set30_month_never_explains_the_comparison_basis():
    legacy = build(sov_coverage=_coverage(unmatched=2))
    view = build(sov_coverage=_set30_coverage(unmatched=2))

    assert legacy.exposure.platform_note  # 옛 15개 달은 종전 그대로
    assert view.exposure.platform_note == ""
    printed = "\n".join(str(text) for page in view.required_texts() for text in page)
    for phrase in MARKETING_REMOVED_COPY:
        assert phrase not in printed


def test_legacy_month_keeps_the_placeholder_a_row_and_its_facts():
    view = build()

    rows = {row.group: row for row in view.exposure.group_rows}
    assert rows["A"].rate is None and rows["A"].keyword_count == 0
    assert view.exposure.group_note == ""


def test_stored_summary_rebuilds_the_set30_facts():
    coverage = _set30_coverage()
    scenario = {"sov_coverage": coverage}
    view = build(**scenario)
    sov, content = _stored(view, scenario)

    stored = stored_facts_v4(sov, content)

    assert compare_facts_v4(stored, view.facts()) == []
    assert stored["card.keywords"] == "4개 중 2개"
    assert stored["kw.가상동 튼튼내과의원 어때?.total"] == "5/6"


def test_set30_view_renders_and_validates_as_a_pdf():
    view = build(sov_coverage=_set30_coverage())
    expectation = build_doctor_pdf_expectation(view, PUBLIC_URL)

    rendered = render_validated_doctor_pdf(
        view=view, period_label=view.period_ym, public_url=PUBLIC_URL, expectation=expectation
    )

    assert rendered.metadata.validation_version == "doctor-pdf-v4"
    assert rendered.pdf_bytes.startswith(b"%PDF")


def test_stored_group_and_accuracy_facts_are_checked_against_the_view():
    coverage = _set30_coverage()
    scenario = {"sov_coverage": coverage}
    view = build(**scenario)
    sov, content = _stored(view, scenario)
    stored = stored_facts_v4(sov, content)

    assert stored["group.A"] == "AI 답변 12건 중 11건"
    assert stored["group.A.accuracy.주소"] == "5건 중 4건"
    assert view.facts()["group.A.accuracy.진료 항목"] == "3건 중 3건"
    tampered = {**view.facts(), "group.A": "AI 답변 999건 중 99건"}
    assert any(problem.startswith("group.A:") for problem in compare_facts_v4(stored, tampered))


def test_ae_label_rebuild_never_counts_brand_rows():
    from app.services.doctor_report_v4 import (
        treatment_bars_from_appendix,
        treatment_card_from_appendix,
    )

    view = build(sov_coverage=_set30_coverage())
    rows = view.appendix.keyword_rows
    card = treatment_card_from_appendix(rows)
    bars = treatment_bars_from_appendix(rows)

    assert card.value == view.summary.cards[3].value
    assert {bar.label for bar in bars} == {bar.label for bar in view.exposure.treatment_bars}


def test_old_payload_ignores_the_current_target_group():
    from test_doctor_report_v4 import _targets

    targets = _targets()
    for target in targets:
        target.question_group = "D"  # 지금 30개 세트로 옮겼어도
    view = build(query_targets=targets)  # 그룹 없는 옛 payload(10월)

    groups = {row.keyword: row.group for row in view.appendix.keyword_rows}
    assert groups["가상동 심장초음파 잘하는 병원"] == "B"


def test_brand_answers_never_become_cases_or_competition_evidence():
    from types import SimpleNamespace

    from test_doctor_report_v4 import _records

    brand_record = SimpleNamespace(
        id="brand-1",
        query_text="가상동 튼튼내과의원 어때?",
        report_query_text="가상동 튼튼내과의원 어때?",
        platform="chatgpt",
        is_mentioned=True,
        measurement_status="SUCCESS",
        raw_response="가상동 튼튼내과의원은 좋은 병원입니다. " * 5,
        mention_context="가상동 튼튼내과의원은 좋은 병원입니다.",
        matched_text="가상동 튼튼내과의원",
        competitor_mentions=[{"name": "가상동 경쟁내과", "is_mentioned": False}],
        source_urls=[],
        citation_urls=[],
    )
    legacy = build(sov_coverage=_set30_coverage())
    view = build(sov_coverage=_set30_coverage(), records=[brand_record, *_records()])

    assert view.cases.found is None or "튼튼내과의원 어때" not in (view.cases.found.keyword or "")
    assert view.competition == legacy.competition

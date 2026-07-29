"""언급률 분모에서 '이길 수 없는 질문'을 빼는 계약.

배경(2026-07-29 실측):
- 지역 의도 질문("성수동 무릎 통증 잘하는 곳")에서 AI가 부른 기관 75곳 중
  의원·전문 63곳(84%). 동네 의원이 실제로 불린다 — 콘텐츠로 들어갈 자리가 있다.
- 지역 없는 의학 설명 질문("무릎 통증 초기 증상이 뭔지 알려줘")에서는 Mayo Clinic·
  대학병원을 인용한다. 병원이 무엇을 하든 언급률 0으로 고정이다.
- 그런데 전체 324개 질문 중 INFO가 72개(22%)로 같은 분모에 있었다 → 우리가 우리
  성과를 22% 깎아서 보고하고 있었다.
"""

from __future__ import annotations

from app.services.sov_engine import (
    MENTION_RATE_INTENTS,
    QUERY_INTENT_INFO,
    QUERY_INTENT_LOCAL,
    calculate_sov,
    classify_query_intent,
    generate_query_matrix,
    generate_query_matrix_specs,
    segment_mention_rates,
)

REGION = ["서울", "성동구"]
SPECIALTIES = ["정형외과"]
KEYWORDS = ["무릎 통증"]


def test_info_queries_are_excluded_from_the_headline_mention_rate() -> None:
    """핵심 계약: INFO는 분모에 들어가지 않는다.

    LOCAL 2건 중 1건 언급 → 50%. INFO 2건(둘 다 미언급)이 섞여도 50%여야 한다.
    옛 동작이면 1/4 = 25%로 절반이 깎인다.
    """
    results = [
        {"is_mentioned": True, "query_intent": QUERY_INTENT_LOCAL},
        {"is_mentioned": False, "query_intent": QUERY_INTENT_LOCAL},
        {"is_mentioned": False, "query_intent": QUERY_INTENT_INFO},
        {"is_mentioned": False, "query_intent": QUERY_INTENT_INFO},
    ]
    assert calculate_sov(results) == 50.0
    # 유형을 가리지 않으면 옛 계산과 같다 — 운영 진단용으로 남겨둔 경로.
    assert calculate_sov(results, intents=None) == 25.0


def test_records_without_an_intent_stay_in_the_denominator() -> None:
    """fail-open: 유형 미상을 빼버리면 실제 성과가 리포트에서 조용히 사라진다."""
    results = [
        {"is_mentioned": True},
        {"is_mentioned": False},
    ]
    assert calculate_sov(results) == 50.0


def test_failed_measurements_are_still_excluded_alongside_intent_filtering() -> None:
    """유형 필터가 붙어도 기존 계약(실패는 분모 제외)이 살아 있어야 한다."""
    results = [
        {"is_mentioned": True, "query_intent": QUERY_INTENT_LOCAL},
        {"is_mentioned": False, "query_intent": QUERY_INTENT_LOCAL,
         "measurement_status": "FAILED"},
    ]
    assert calculate_sov(results) == 100.0


def test_no_successful_local_measurement_returns_none_not_zero() -> None:
    """INFO만 성공했다면 '언급률 0%'가 아니라 '측정 안 됨'이다."""
    results = [{"is_mentioned": False, "query_intent": QUERY_INTENT_INFO}]
    assert calculate_sov(results) is None


def test_info_segment_is_reported_not_discarded() -> None:
    """헤드라인에서 빠진 구간도 원장에게 설명 가능해야 한다."""
    results = [
        {"is_mentioned": True, "query_intent": QUERY_INTENT_LOCAL},
        {"is_mentioned": False, "query_intent": QUERY_INTENT_LOCAL},
        {"is_mentioned": False, "query_intent": QUERY_INTENT_INFO},
    ]
    seg = segment_mention_rates(results)
    assert seg[QUERY_INTENT_LOCAL] == {"measured": 2, "mentioned": 1, "mention_rate": 50.0}
    assert seg[QUERY_INTENT_INFO] == {"measured": 1, "mentioned": 0, "mention_rate": 0.0}


def test_templates_without_a_region_are_tagged_info() -> None:
    """유형 태깅이 실제 템플릿과 일치하는가 — 지역 치환자 유무가 근거다."""
    from app.services.sov_engine import _TEMPLATE_SPECS

    for template, intent in _TEMPLATE_SPECS:
        has_region = "{region}" in template or "{sub_region}" in template
        expected = QUERY_INTENT_LOCAL if has_region else QUERY_INTENT_INFO
        assert intent == expected, f"{template!r} 의 유형이 지역 치환자 유무와 어긋난다"


def test_info_templates_are_a_meaningful_share_worth_separating() -> None:
    """분리할 가치가 있는 규모인지 고정 — 22%였다."""
    from app.services.sov_engine import _TEMPLATE_SPECS

    info = sum(1 for _, intent in _TEMPLATE_SPECS if intent == QUERY_INTENT_INFO)
    assert info >= 4, "INFO 템플릿이 사라졌다면 분모 분리 자체를 재검토해야 한다"


def test_generated_specs_carry_intent_and_match_plain_generation() -> None:
    specs = generate_query_matrix_specs(REGION, SPECIALTIES, KEYWORDS)
    texts = generate_query_matrix(REGION, SPECIALTIES, KEYWORDS)

    assert sorted(text for text, _ in specs) == sorted(texts)
    assert {intent for _, intent in specs} == {QUERY_INTENT_LOCAL, QUERY_INTENT_INFO}
    for text, intent in specs:
        if intent == QUERY_INTENT_INFO:
            assert not any(r in text for r in REGION), f"INFO인데 지역이 들어있다: {text}"


def test_classify_recovers_intent_from_text_for_untemplated_queries() -> None:
    """AE가 직접 등록한 질문(AIQueryTarget variant)도 유형이 잡혀야 한다."""
    assert classify_query_intent("무릎 통증 초기 증상이 뭔지 알려줘") == QUERY_INTENT_INFO
    assert classify_query_intent("어깨 통증 치료 비용이 얼마나 드는지 알려줘") == QUERY_INTENT_INFO
    assert classify_query_intent("성수동 무릎 통증 잘하는 곳") == QUERY_INTENT_LOCAL
    # 판별 불가 → LOCAL(fail-open)
    assert classify_query_intent("아무 말이나") == QUERY_INTENT_LOCAL
    assert classify_query_intent("") == QUERY_INTENT_LOCAL


def test_classify_agrees_with_the_template_tags_it_was_derived_from() -> None:
    """텍스트 기반 분류가 생성 시점 태그와 어긋나면 백필과 신규가 갈린다."""
    for text, intent in generate_query_matrix_specs(REGION, SPECIALTIES, KEYWORDS):
        assert classify_query_intent(text) == intent, text


def test_mention_rate_intents_is_local_only() -> None:
    assert MENTION_RATE_INTENTS == frozenset({QUERY_INTENT_LOCAL})


def test_v0_sample_is_large_enough_for_a_diagnosis_number() -> None:
    """V0는 원장에게 처음 보여주는 '진단서'다.

    5질문 × 5반복 = 플랫폼당 25관측이면 1건 차이로 4%p씩 튄다. 진단이라고 부르며
    파는 숫자의 오차로는 너무 크다.
    """
    from app.workers import tasks

    observations = tasks.V0_QUERY_SAMPLE_COUNT * tasks.V0_REPEAT_COUNT
    assert observations >= 50, (
        f"플랫폼당 관측이 {observations}건이면 1건당 {100/observations:.1f}%p씩 움직인다"
    )


def test_v0_samples_only_from_queries_that_count_toward_the_headline() -> None:
    """INFO 질문을 V0 표본에 넣으면 호출만 쓰고 헤드라인에는 기여하지 않는다."""
    import inspect

    from app.workers import tasks

    source = inspect.getsource(tasks.v0_sample_query_stmt)
    assert "query_intent" in source, "V0 표본이 질문 유형을 가리지 않는다"


def test_the_dead_monthly_repeat_setting_is_gone() -> None:
    """월간 측정이라는 것이 없는데 'SOV_REPEAT_COUNT'가 있으면 운영자를 오도한다.

    월간 리포트는 주간 기록을 집계할 뿐이다 — 이 설정을 바꿔도 아무 일도 일어나지 않았다.
    """
    from app.core.config import Settings

    assert not hasattr(Settings(), "SOV_REPEAT_COUNT"), (
        "앱이 읽지 않는 설정이 되살아났다 — 주간 반복만 실재한다"
    )
    assert hasattr(Settings(), "SOV_REPEAT_COUNT_WEEKLY")


def test_ae_registered_variant_queries_are_classified_too() -> None:
    """AIQueryTarget variant는 템플릿을 거치지 않는다 — 여기서 분류를 빠뜨리면
    AE가 등록한 정보성 질문이 LOCAL로 들어가 분모 분리가 조용히 무력화된다."""
    import inspect

    from app.workers import tasks

    source = inspect.getsource(tasks._ensure_variant_query_matrix)
    assert "classify_query_intent" in source, (
        "variant 유래 QueryMatrix가 유형 없이 생성된다"
    )

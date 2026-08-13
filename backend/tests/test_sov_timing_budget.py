"""SoV 측정의 시간 예산 불변식.

이 파일이 존재하는 이유(2026-07-29):
공급자 타임아웃이 30초였는데 실측 p50이 chatgpt 24.7~62.8초였다. 정상 응답이
타임아웃으로 잘려 FAILED로 기록됐고, calculate_sov가 FAILED를 분모에서 빼므로
원장에게 보고되는 언급률이 "30초 안에 끝난 소수"만으로 계산됐다. 그 소수는 검색을
덜 한 짧은 답변이라 병원 이름이 적게 나온다 — 편향이 한 방향으로 쏠린다.

여기 테스트는 값을 자기 자신과 비교하지 않는다(그러면 아무것도 증명하지 못한다).
**서로 독립적으로 정해지는 값들 사이의 제약**을 건다:
  - 타임아웃  ↔  실측 지연시간
  - 물량(상한 × 반복) ↔ 동시 실행 수 ↔ 태스크 시간 한도
어느 한쪽만 바꾸면 실패한다.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import sov_engine

# 2026-07-29 실측. 지역 의도 질문 5종 × 2회, 웹검색 강제, 각 10/10 성공.
# 재측정하면 이 표를 갱신할 것 — 근거 없는 숫자를 두지 않기 위해 출처를 함께 남긴다.
MEASURED_LATENCY_SECONDS = {
    "openai:gpt-5-mini-2025-08-07": {"p50": 62.8, "p90": 86.8, "max": 86.8},
    "openai:gpt-5.6-luna": {"p50": 24.7, "p90": 34.8, "max": 34.8},
    "gemini:gemini-3.6-flash": {"p50": 7.9, "p90": 10.4, "max": 10.4},
}

WORST_OPENAI_P90 = max(
    v["p90"] for k, v in MEASURED_LATENCY_SECONDS.items() if k.startswith("openai:")
)
WORST_GEMINI_P90 = max(
    v["p90"] for k, v in MEASURED_LATENCY_SECONDS.items() if k.startswith("gemini:")
)


def test_openai_timeout_exceeds_measured_p90_latency() -> None:
    """타임아웃이 실측 p90보다 짧으면 정상 응답의 10% 이상을 버린다."""
    assert sov_engine.OPENAI_TIMEOUT_SECONDS > WORST_OPENAI_P90, (
        f"OPENAI_TIMEOUT_SECONDS={sov_engine.OPENAI_TIMEOUT_SECONDS}s 는 실측 p90 "
        f"{WORST_OPENAI_P90}s 보다 짧다. 정상 응답이 FAILED로 기록되고 언급률이 "
        f"편향된 표본으로 계산된다."
    )


def test_gemini_timeout_exceeds_measured_p90_latency() -> None:
    assert sov_engine.GEMINI_TIMEOUT_SECONDS > WORST_GEMINI_P90, (
        f"GEMINI_TIMEOUT_SECONDS={sov_engine.GEMINI_TIMEOUT_SECONDS}s 는 실측 p90 "
        f"{WORST_GEMINI_P90}s 보다 짧다."
    )


def test_gemini_client_and_wait_for_share_one_timeout_constant() -> None:
    """Gemini 경로에는 타임아웃이 두 군데 있다 — 한쪽만 고치면 짧은 쪽이 이긴다."""
    source = (sov_engine.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    assert "timeout=30.0" not in text, "하드코딩된 30초 타임아웃이 남아 있다"
    assert '"timeout": 30000' not in text, "Gemini 클라이언트에 하드코딩된 30초가 남아 있다"
    assert text.count("GEMINI_TIMEOUT_SECONDS") >= 3, (
        "Gemini 타임아웃 상수가 클라이언트와 wait_for 양쪽에 쓰이지 않는다"
    )


def _weekly_worst_case_calls() -> int:
    """주간 측정 1개 병원의 최대 공급자 호출 수."""
    return settings.SOV_HIGH_PRIORITY_CAP * settings.SOV_REPEAT_COUNT_WEEKLY


@pytest.mark.parametrize("task_name", ["run_sov_for_hospital", "trigger_v0_report"])
def test_measurement_fits_inside_its_celery_soft_time_limit(task_name: str) -> None:
    """물량 ÷ 동시성 × 실측 p50 이 태스크 soft_time_limit 안에 들어와야 한다.

    셋 중 하나만 바꾸면(상한 ↑, 동시성 ↓, 반복 ↑) 태스크가 중간에 죽고
    MeasurementRun이 RUNNING으로 남는다.
    """
    from app.workers import tasks

    task = getattr(tasks, task_name)
    soft_limit = task.soft_time_limit
    assert soft_limit, f"{task_name}에 soft_time_limit이 없다 (전역 600초가 적용된다)"

    if task_name == "trigger_v0_report":
        calls = tasks.V0_QUERY_SAMPLE_COUNT * tasks.V0_REPEAT_COUNT * 2  # 2 플랫폼
    else:
        calls = _weekly_worst_case_calls()

    # 최악의 모델(가장 느린 실측 p50)을 기준으로 잡는다.
    slowest_p50 = max(v["p50"] for v in MEASURED_LATENCY_SECONDS.values())
    waves = -(-calls // sov_engine.SOV_PROVIDER_CONCURRENCY)  # ceil
    estimated = waves * slowest_p50

    assert estimated < soft_limit, (
        f"{task_name}: 호출 {calls}건 ÷ 동시 {sov_engine.SOV_PROVIDER_CONCURRENCY} "
        f"× p50 {slowest_p50}s = 약 {estimated:.0f}s 로 soft_time_limit {soft_limit}s를 "
        f"넘는다. 동시성을 올리거나 물량 상한(SOV_HIGH_PRIORITY_CAP="
        f"{settings.SOV_HIGH_PRIORITY_CAP}) 을 낮춰라."
    )


@pytest.mark.asyncio
async def test_provider_concurrency_is_actually_used_by_the_semaphore() -> None:
    """상수만 있고 Semaphore가 옛 값을 쓰면 동시성 조정이 무의미해진다.

    소스 텍스트가 아니라 **만들어진 세마포어의 실제 용량**을 본다 — 문자열 검사는
    구현을 조금만 바꿔도 통과하거나 깨지는데, 둘 다 잘못된 신호다.
    """
    sov_engine._api_semaphores.clear()
    semaphore = sov_engine._get_semaphore(sov_engine.POOL_SOV)
    assert semaphore._value == sov_engine.SOV_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_leadgen_and_paid_measurement_do_not_share_a_concurrency_pool() -> None:
    """무료 진단이 유료 측정의 슬롯을 굶기면 안 된다 (PRD F6-1 · 설계 T-13).

    큐만 분리하고 세마포어를 공유하면 이 요구는 코드 수준에서 지켜지지 않는다.
    선착순 마케팅은 오픈 직후 신청을 몰리게 만드는 것이 목적이라, 그 순간이 정확히
    유료 고객 측정이 느려지는 순간이 된다.
    """
    sov_engine._api_semaphores.clear()
    paid = sov_engine._get_semaphore(sov_engine.POOL_SOV)
    free = sov_engine._get_semaphore(sov_engine.POOL_LEADGEN)

    assert paid is not free
    assert free._value == settings.LEADGEN_PROVIDER_CONCURRENCY

    # 무료 풀을 완전히 소진시켜도 유료 풀의 잔여 슬롯이 줄지 않는다.
    for _ in range(settings.LEADGEN_PROVIDER_CONCURRENCY):
        await free.acquire()
    assert free.locked()
    assert paid._value == sov_engine.SOV_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_run_single_query_defaults_to_the_paid_pool() -> None:
    """기본값이 leadgen이면 기존 유료 경로가 조용히 무료 풀로 옮겨간다."""
    import inspect

    default = inspect.signature(sov_engine.run_single_query).parameters["pool"].default
    assert default == sov_engine.POOL_SOV


def test_both_platforms_receive_the_identical_prompt() -> None:
    """플랫폼 비대칭 회귀 방지.

    2026-07-29 이전: ChatGPT만 "구체적인 병원 이름을 포함해 답변하세요" 지시를 받고
    Gemini는 질문만 받았다. 그 상태의 "ChatGPT n% vs Gemini m%"는 플랫폼 차이가 아니라
    우리 프롬프트 차이의 결과였다.
    """
    source = (sov_engine.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()

    # 양쪽 모두 **같은 상수를 같은 역할(지시문 파라미터)로** 보내야 한다.
    # 문자열만 같고 역할이 다르면(한쪽은 지시문, 한쪽은 질문에 이어붙임) 그것도 비대칭이다.
    assert "instructions=SYSTEM_PROMPT_SOV" in text, "OpenAI 경로가 지시문을 지시문 자리로 안 보낸다"
    assert "system_instruction=SYSTEM_PROMPT_SOV" in text, "Gemini 경로가 지시문을 지시문 자리로 안 보낸다"
    # 한쪽에만 프롬프트를 직접 끼워 넣는 옛 형태가 남아 있으면 안 된다.
    assert "SYSTEM_PROMPT_CHATGPT" not in text, "플랫폼 전용 프롬프트 상수가 남아 있다"
    # 지시문을 질문 문자열에 이어붙이던 옛 전달 방식이 남아 있으면 안 된다 —
    # 리포트가 "시스템 지시문"이라고 공개하는 것과 실제 호출이 어긋난다.
    assert "build_sov_prompt" not in text, "지시문을 질문에 이어붙이는 옛 경로가 남아 있다"


def test_measurement_protocol_records_how_the_prompt_is_delivered() -> None:
    """같은 지시문도 전달 역할이 다르면 다른 측정이다 — 비교 게이트가 이를 봐야 한다."""
    protocol = sov_engine.measurement_protocol()

    assert protocol["prompt_delivery"] == "system_role"
    drifted = {**protocol, "prompt_delivery": "prepended"}
    assert not sov_engine.same_measurement_policy(protocol, drifted)


def test_gemini_output_cap_is_not_the_binding_constraint() -> None:
    """Gemini만 출력이 짧게 잘리면 병원 이름이 나올 자리가 줄어 언급률이 낮게 나온다."""
    measured_openai_max_output = 3051  # 2026-07-29 실측
    assert sov_engine.GEMINI_MAX_OUTPUT_TOKENS > measured_openai_max_output, (
        f"GEMINI_MAX_OUTPUT_TOKENS={sov_engine.GEMINI_MAX_OUTPUT_TOKENS} 가 OpenAI 실측 "
        f"출력 {measured_openai_max_output}토큰보다 작다 — Gemini만 잘린다."
    )

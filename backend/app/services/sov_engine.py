"""AI 답변 언급률 엔진 — 환자 질문 생성·발송·파싱·계산"""

import asyncio
import hashlib
import json
import logging
import re
import threading
from contextvars import ContextVar
from itertools import product
from typing import Any
from urllib.parse import urlparse

from google import genai as google_genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

# 동시성 풀 이름. 유료 측정(sov)과 무료 진단(leadgen)이 **다른 풀**을 쓴다.
#
# 큐만 분리하면(PRD F6-1) 이 요구가 지켜지지 않는다 — 세마포어가 전역이면 무료 진단이
# 몰릴 때 유료 고객의 주간 측정이 같은 슬롯을 두고 굶는다. 선착순 마케팅은 오픈 직후
# 신청을 몰리게 만드는 것이 목적이므로, 그 순간이 정확히 유료 경로가 느려지는 순간이 된다.
POOL_SOV = "sov"
POOL_LEADGEN = "leadgen"

# 실제 공급자 호출을 어느 예산으로 계수할지. 풀 이름이 곧 cost_guard 카테고리라
# 무료 진단(leadgen)과 유료 측정(sov)의 실제 지출이 섞이지 않는다.
_provider_cost_category: ContextVar[str] = ContextVar("sov_provider_cost_category", default=POOL_SOV)


async def _record_sov_provider_call(count: int = 1) -> None:
    """실제로 나간 공급자 호출을 기록한다(차단하지 않음).

    예약(check_and_increment)은 호출부가 측정 시작 전에 한 번만 한다. 그런데 이 모듈의
    질의 함수는 tenacity로 최대 3회 재시도되고 `_query_gemini`는 `_query_gemini_result`를
    감싸 중첩 재시도까지 있어, 실제 호출이 예약보다 몇 배 많아질 수 있다. 그 차이를
    기록해 두지 않으면 상한이 실제 지출을 막고 있는지 판단할 근거 자체가 없다.
    """
    from app.services import cost_guard

    await cost_guard.record_provider_call(_provider_cost_category.get(), count=count)

_sem_lock = threading.Lock()
_api_semaphores: dict[str, asyncio.Semaphore] = {}
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _pool_limit(pool: str) -> int:
    if pool == POOL_LEADGEN:
        return settings.LEADGEN_PROVIDER_CONCURRENCY
    return SOV_PROVIDER_CONCURRENCY


def _get_semaphore(pool: str = POOL_SOV) -> asyncio.Semaphore:
    """Lazily create a per-pool semaphore bound to the current event loop.
    Thread-safe: uses a lock for creation. Recreates if the loop changed.
    """
    global _semaphore_loop
    current_loop = asyncio.get_running_loop()
    with _sem_lock:
        if _semaphore_loop is not current_loop:
            _api_semaphores.clear()
            _semaphore_loop = current_loop
        if pool not in _api_semaphores:
            _api_semaphores[pool] = asyncio.Semaphore(_pool_limit(pool))
        return _api_semaphores[pool]


# 공급자 호출은 전부 I/O 대기다 — 동시 실행 수가 곧 측정 태스크의 벽시계 시간을 정한다.
# 주간 측정(질의 spec × 반복 5회)이 Celery task_time_limit 안에 끝나려면 5로는 부족하다.
# 실측(2026-07-29, 지역 의도 질문 10회): chatgpt p50 24.7s(luna) / 62.8s(mini),
# gemini p50 7.9s. 5동시로는 주간 300호출이 한도를 넘고, 10이면 들어온다.
SOV_PROVIDER_CONCURRENCY = 10

# 웹검색을 강제한 추론 모델의 실제 소요 시간에 맞춘다.
#
# 30초였을 때 무슨 일이 있었나: 실측 p50이 chatgpt 24.7~62.8초라, 30초 타임아웃은
# 정상 응답을 대부분 잘라내고 FAILED로 기록했다. calculate_sov가 FAILED를 분모에서
# 빼므로, 원장에게 보고되는 언급률이 "30초 안에 끝난 소수"만으로 계산됐다.
# 그 소수는 무작위 표본이 아니다 — 검색을 덜 한 짧은 답변이라 병원 이름이 적게 나온다.
# 게다가 느린 질의일수록 지역 의도 질의(= 병원이 언급될 자리)라 편향이 한 방향이다.
#
# 값 근거: 관측 최대 86.8초(mini) 대비 여유. 재시도 3회와 곱해지므로 무한정 늘리지 않는다.
OPENAI_TIMEOUT_SECONDS = 120.0
# Gemini는 실측 p50 7.9s / 최대 10.4s로 훨씬 빠르다. 여유만 두고 과하게 늘리지 않는다.
GEMINI_TIMEOUT_SECONDS = 60.0

openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=OPENAI_TIMEOUT_SECONDS)
_gemini_client: google_genai.Client | None = None


def _get_gemini_client() -> google_genai.Client | None:
    global _gemini_client
    if settings.GEMINI_API_KEY and _gemini_client is None:
        _gemini_client = google_genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options={"timeout": int(GEMINI_TIMEOUT_SECONDS * 1000)},  # ms
        )
    return _gemini_client


# 질문 유형 — 언급률 분모를 가르는 기준.
#
# LOCAL: 지역이 들어간 "어느 병원 갈까" 질문. AI가 구체적인 동네 의원 이름을 댄다.
#        실측(2026-07-29, 성수동/성동구 질문 4종 × 2회): 언급 기관 75곳 중 의원·전문
#        63곳(84%), 대형병원 12곳(16%). 콘텐츠로 이 자리에 들어갈 수 있다.
# INFO:  지역이 없는 의학 설명 질문("무릎 통증 초기 증상이 뭔지 알려줘").
#        AI가 특정 의원 이름을 댈 이유가 없고 실제로 Mayo Clinic·대학병원을 인용한다.
#        병원이 무엇을 하든 언급률 0으로 고정이다.
#
# 왜 나누는가: 전체 324개 질문 중 INFO가 72개(22%)인데 같은 분모에 있었다. 병원이
# 이길 수 없는 질문이 분모를 부풀려 **우리가 우리 성과를 22% 깎아서 보고**하고 있었다.
# INFO 질문에서의 성과는 "이름이 불리는 것"이 아니라 "우리 콘텐츠가 출처로 인용되는
# 것"이라 애초에 다른 지표다 — 하나로 뭉개면 둘 다 못 읽는다.
QUERY_INTENT_LOCAL = "LOCAL"
QUERY_INTENT_INFO = "INFO"

# 언급률(원장 보고 헤드라인)의 분모에 들어가는 유형.
MENTION_RATE_INTENTS = frozenset({QUERY_INTENT_LOCAL})

_TEMPLATE_SPECS: list[tuple[str, str]] = [
    # 추천형
    ("{region} {keyword} 잘 보는 병원 추천해줘", QUERY_INTENT_LOCAL),
    ("{region} {specialty} 어디가 좋아", QUERY_INTENT_LOCAL),
    ("{sub_region} {keyword} 잘하는 곳", QUERY_INTENT_LOCAL),
    ("{region} {specialty} 전문의 추천", QUERY_INTENT_LOCAL),
    ("{keyword} 수술 {region} 어느 병원이 좋아?", QUERY_INTENT_LOCAL),
    ("{region} {keyword} 치료 잘하는 병원", QUERY_INTENT_LOCAL),
    # 증상·탐색형
    ("{keyword} 증상 {region}에서 치료 잘하는 곳", QUERY_INTENT_LOCAL),
    ("{keyword} 있는데 {region} 어느 병원 가야 해?", QUERY_INTENT_LOCAL),
    ("{keyword} 초기 증상이 뭔지 알려줘", QUERY_INTENT_INFO),
    ("{keyword} 치료하려면 어떤 전문의한테 가야 해?", QUERY_INTENT_INFO),
    ("{region} {keyword} 빨리 낫는 병원", QUERY_INTENT_LOCAL),
    # 비교형
    ("{region} {specialty} 병원 어디가 좋은지 비교해줘", QUERY_INTENT_LOCAL),
    ("{region} {keyword} 병원 후기 좋은 곳", QUERY_INTENT_LOCAL),
    ("{sub_region} {specialty} 잘한다고 소문난 병원", QUERY_INTENT_LOCAL),
    # 비용·정보형
    ("{keyword} 치료 비용이 얼마나 드는지 알려줘", QUERY_INTENT_INFO),
    ("{keyword} 수술 후 회복 기간 얼마나 돼?", QUERY_INTENT_INFO),
    ("{keyword} 비수술 치료 가능한 병원 {region}", QUERY_INTENT_LOCAL),
    ("{region} {specialty} 비용 어느 정도야?", QUERY_INTENT_LOCAL),
]

QUERY_TEMPLATES = [template for template, _ in _TEMPLATE_SPECS]

# 템플릿의 고정부(치환자를 뺀 부분)로 기존 질문 텍스트의 유형을 되찾는다.
# 마이그레이션 백필과, 템플릿을 거치지 않고 들어온 AIQueryTarget 질문에 쓴다.
_INFO_TEMPLATE_MARKERS = tuple(
    sorted(
        (
            max(
                (part for part in template.replace("{keyword}", "\x00").split("\x00")),
                key=len,
            ).strip()
            for template, intent in _TEMPLATE_SPECS
            if intent == QUERY_INTENT_INFO
        ),
        key=len,
        reverse=True,
    )
)


def classify_query_intent(query_text: str) -> str:
    """질문 텍스트의 유형. 판별 못 하면 LOCAL(분모 포함)로 둔다.

    fail-open인 이유: 잘못 INFO로 빼면 실제 성과가 리포트에서 조용히 사라진다.
    잘못 LOCAL로 두면 기존과 같은 희석일 뿐이라, 두 오류의 무게가 다르다.
    """
    text = (query_text or "").strip()
    for marker in _INFO_TEMPLATE_MARKERS:
        if marker and marker in text:
            return QUERY_INTENT_INFO
    return QUERY_INTENT_LOCAL

# 자사·경쟁사 판정에 동일하게 들어가는 동일성 기준. 두 프롬프트가 서로 다른 기준을 쓰면
# "우리 병원 vs 경쟁 병원" 비교가 사과와 오렌지가 된다 — 원장 보고서의 핵심 지표이므로
# 문구를 한 곳에서 정의해 양쪽에 주입한다.
_IDENTITY_RULE = """\
띄어쓰기·의원/병원 접미사 차이처럼 동일 기관임이 명확한 표기 변형만 인정한다.
흔한 앞글자 2~3자가 같거나 다른 지역의 동명 기관인 것만으로는 동일 병원으로 간주하지 않는다."""

# 판정 3값 (PRD F3-7). 이진 판정은 "확정할 수 없음"을 표현할 자리가 없어
# 경계 사례를 전부 true/false 중 하나로 접는다. 판정기는 애매하면 true로 기울고,
# 그 편향이 그대로 언급률이 된다 — 그래서 "모르겠다"를 1급 결과로 둔다.
VERDICT_MATCHED = "MATCHED"
VERDICT_NOT_MATCHED = "NOT_MATCHED"
VERDICT_AMBIGUOUS = "AMBIGUOUS"
_VERDICTS = (VERDICT_MATCHED, VERDICT_NOT_MATCHED, VERDICT_AMBIGUOUS)

PARSE_PROMPT = f"""\
다음 AI 답변이 "{{hospital_name}}"(소재 지역: {{region}})을 언급했는지 판정하라.
{_IDENTITY_RULE}

판정값은 셋 중 하나다:
- MATCHED: 답변에 이 병원을 가리키는 표현이 있고, 정식 명칭이거나 명확한 표기 변형이며,
  지역 정보가 충돌하지 않는다.
- NOT_MATCHED: 후보 표현이 없거나, 다른 기관임이 분명하다.
- AMBIGUOUS: 동명 기관일 수 있거나, 타지역 기관일 수 있거나, 접미사를 뗀 흔한 표현만
  등장하거나, 이름 일부만 나와 동일 기관인지 확정할 수 없다.

진료과 이름·일반 형용사·흔한 접두어만 겹치는 것으로 MATCHED를 주지 마라.
확신이 서지 않으면 추측하지 말고 반드시 AMBIGUOUS를 골라라.
matched_text에는 답변에서 **그대로 잘라낸** 표현만 넣는다. 지어내지 마라.

[답변]
{{response}}

반드시 아래 JSON만 출력:
{{{{"verdict": "MATCHED"/"NOT_MATCHED"/"AMBIGUOUS", "matched_text": "답변에서 인용한 표현 또는 null", "mention_rank": null 또는 정수, "sentiment": "positive"/"neutral"/"negative"/null, "mention_context": "언급 문장 또는 null"}}}}"""

COMPETITOR_PARSE_PROMPT = f"""\
다음 AI 답변에서 아래 병원들이 각각 언급되었는지 분석하라.
{_IDENTITY_RULE}

[분석 대상 병원 목록]
{{competitor_names}}

[답변]
{{response}}

반드시 아래 JSON 객체만 출력:
{{{{"competitors": [{{{{"name": "병원명", "is_mentioned": true/false, "mention_rank": null 또는 정수}}}}]}}}}"""


def generate_query_matrix(
    region: list[str], specialties: list[str], keywords: list[str]
) -> list[str]:
    # 🔴 CRITICAL fix: empty inputs cause product() to yield zero combinations,
    # returning an empty list. Without this guard, V0 report runs with 0 queries
    # and produces a meaningless 0% AI mention-rate result silently.
    if not keywords or not specialties:
        logger.warning(
            f"generate_query_matrix called with empty inputs: "
            f"region={region}, specialties={specialties}, keywords={keywords}. "
            f"Returning empty query list."
        )
        return []

    return [text for text, _ in generate_query_matrix_specs(region, specialties, keywords)]


def generate_query_matrix_specs(
    region: list[str], specialties: list[str], keywords: list[str]
) -> list[tuple[str, str]]:
    """(질문 텍스트, 질문 유형) 목록.

    유형은 텍스트에서 되짚는 게 아니라 **템플릿에서 그대로 들고 온다** — 생성 시점이
    유형을 가장 확실히 아는 지점이고, 여기서 흘리면 이후로는 추측만 남는다.
    """
    if not keywords or not specialties:
        return []

    seen: dict[str, str] = {}
    main_region = region[0] if region else ""
    sub_region = region[1] if len(region) > 1 else main_region
    for (template, intent), keyword, specialty in product(_TEMPLATE_SPECS, keywords, specialties):
        q = template.format(
            region=main_region, sub_region=sub_region, keyword=keyword, specialty=specialty
        )
        # 서로 다른 템플릿이 같은 문장을 만들면 더 보수적인 쪽(LOCAL)을 남긴다.
        if seen.get(q) != QUERY_INTENT_LOCAL:
            seen[q] = intent
    return list(seen.items())


# 두 플랫폼에 **같은 지시문을 같은 위치로** 준다.
#
# 2026-07-29 이전에는 ChatGPT에만 이 지시문이 붙고 Gemini는 질문만 받았다. 그래서
# "ChatGPT 32% vs Gemini 8%" 같은 숫자가 나와도 그건 플랫폼 차이가 아니라 우리가 두
# 모델을 다르게 대우한 결과였다 — 플랫폼 비교가 성립하지 않았다.
# 이름을 CHATGPT에서 SOV로 바꾼 것은 "이건 한 플랫폼 전용이 아니다"를 코드에 못 박기 위함이다.
# ── 측정 정책 v2 (상향 편향 제거).
#
# v1은 두 군데에서 답변을 병원명 쪽으로 밀고 있었다.
#
#   1. 시스템 프롬프트가 "구체적인 병원 이름을 포함해 답변하세요"라고 **지시**했다.
#      우리가 세는 대상(병원명 등장)을 모델에게 시켜놓고 그 빈도를 노출도라고 판 셈이다.
#   2. `tool_choice="required"`로 매 요청 웹검색을 강제했다. "{지역} 근처 {진료과} 병원
#      추천해줘"에 검색을 강제하면 사실상 지역 병원 디렉터리를 긁어와 나열하게 된다.
#
# v2는 둘 다 놓는다. 도구는 **제공하되 강제하지 않고**, 지시문은 중립으로 되돌린다.
# 모델이 검색을 쓸지 말지 고르는 것까지가 측정 대상이다.
#
# 이 전환은 숫자를 낮추려는 튜닝이 아니라 편향 제거지만, **기준선은 확실히 바뀐다.**
# 그래서 정책 버전을 붙여 어디에나 스냅샷하고, 버전이 다른 두 달은 비교하지 않는다.
# v2.1: 지시문을 질문에 이어붙이던 것을 전용 지시문 파라미터로 옮겼다. 같은 문자열도
# 역할이 다르면 모델 동작이 달라지므로 **기준선이 바뀐다** — 버전을 올려 v2와 비교되지
# 않게 한다. 편향 제거가 아니라 재현성 계약(공개 조건 = 실제 조건) 수정이다.
MEASUREMENT_POLICY_VERSION = "v2.1-neutral-auto-systemrole"

# 두 측정이 "같은 조건"인지 판정하는 키 **전부**. 지문 계산과 비교 게이트가 같은
# 목록을 봐야 한다 — 하나에만 키를 추가하면, 조건이 바뀌었는데 비교 게이트는
# 통과하는(또는 그 반대의) 상태가 조용히 만들어진다.
_PROTOCOL_IDENTITY_KEYS = (
    "policy_version",
    "prompt_fingerprint",
    "prompt_delivery",
    "openai_tool_choice",
    "judge_prompt_fingerprint",
    "judge_model",
)

SYSTEM_PROMPT_SOV = (
    "질문에 정확하고 유용하게 답하십시오. "
    "특정 병원을 제시할 경우 확인 가능한 근거에 기반하십시오."
)

# 도구는 준다. 쓸지는 모델이 정한다.
OPENAI_SEARCH_TOOL_CHOICE = "auto"


# 지시문을 어떻게 전달하는가. 이전 정책은 질문 문자열에 이어붙였고(`prepended`),
# 리포트는 그것을 "시스템 지시문"이라고 인쇄했다 — 공개한 조건과 실제 호출이 달랐다.
# v2.1부터 양 플랫폼 모두 전용 지시문 파라미터로 보낸다.
PROMPT_DELIVERY = "system_role"


def measurement_protocol() -> dict:
    """이 측정이 어떤 조건에서 이뤄졌는지 — 접수/동결 시점에 스냅샷할 값 전부.

    **버전 문자열만 두지 않는다.** 사람이 버전을 올리는 것을 잊으면 조건이 바뀐 측정이
    같은 버전으로 기록되고, 그때부터 비교 게이트는 통과하는데 숫자는 못 믿게 된다.
    지시문·판정 프롬프트의 지문을 함께 넣어 잊을 수 없게 만든다.
    """
    return {
        "policy_version": MEASUREMENT_POLICY_VERSION,
        "system_prompt": SYSTEM_PROMPT_SOV,
        "openai_tool_choice": OPENAI_SEARCH_TOOL_CHOICE,
        # 같은 지시문도 전달 역할이 다르면 다른 측정이다.
        "prompt_delivery": PROMPT_DELIVERY,
        "prompt_fingerprint": _fingerprint(SYSTEM_PROMPT_SOV),
        "judge_prompt_fingerprint": _fingerprint(PARSE_PROMPT),
        # 판정 모델도 결과를 바꾼다. 답변 모델은 접수 시점 핀(requested_models)이
        # 별도로 지키지만, 판정 모델은 실행 시점 전역값을 쓰므로 여기 없으면
        # 모델 교체 배포 창에서 리포트가 공개한 판정 모델과 실제가 어긋난다.
        "judge_model": settings.OPENAI_MODEL_PARSE,
    }


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def protocol_fingerprint() -> str:
    """프로토콜 전체의 단일 지문 — 캐시 키와 비교 게이트가 같은 값을 본다."""
    protocol = measurement_protocol()
    material = "|".join(
        f"{key}={protocol[key]}"
        for key in _PROTOCOL_IDENTITY_KEYS
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def same_measurement_policy(left: dict | None, right: dict | None) -> bool:
    """두 측정이 같은 조건이었는가.

    한쪽이라도 기록이 없으면 **다르다고 본다.** 정책 스냅샷 도입 이전 측정은 v1이었고,
    "모르겠다"를 "같다"로 접으면 기준선이 바뀐 두 달을 성과 변화로 붙여 팔게 된다.
    """
    if not left or not right:
        return False
    return all(left.get(key) == right.get(key) for key in _PROTOCOL_IDENTITY_KEYS)


def record_is_confirmed(record) -> bool:
    """이 측정 레코드가 언급률 분모에 들어갈 자격이 있는가 — **모든 집계가 이걸 쓴다.**

    조건: 측정 성공 + 판정 확정(AMBIGUOUS 아님). 소비처마다 조건을 제각각 쓰면
    판정 보류가 어느 화면에서는 미언급, 어느 화면에서는 실패로 계상된다 — 그 불일치가
    이번에 admin/월간/우선순위 세 군데에서 동시에 발견된 결함이다.

    verdict가 NULL인 레거시 행(3값 도입 이전)은 is_mentioned가 bool이므로 확정으로 본다.
    """
    status = getattr(record, "measurement_status", None)
    if str(status or "SUCCESS").upper() != "SUCCESS":
        return False
    if getattr(record, "mention_verdict", None) == VERDICT_AMBIGUOUS:
        return False
    # verdict 없이 is_mentioned만 None인 방어 — 어느 쪽이든 확정이 아니다.
    return getattr(record, "is_mentioned", None) is not None


def record_is_ambiguous(record) -> bool:
    """측정은 성공했지만 판정을 확정하지 못한 레코드 — 실패와 구분해 따로 센다."""
    status = getattr(record, "measurement_status", None)
    if str(status or "SUCCESS").upper() != "SUCCESS":
        return False
    return (
        getattr(record, "mention_verdict", None) == VERDICT_AMBIGUOUS
        or getattr(record, "is_mentioned", None) is None
    )


# Gemini에만 출력 상한이 걸려 있으면 답변이 짧게 잘려 병원 이름이 나올 자리가 줄어든다
# (실측 2026-07-29: Gemini 출력 106토큰 vs OpenAI 1,612~3,051토큰). OpenAI 경로에는
# 상한이 없으므로, Gemini 상한도 실측 최대치를 넉넉히 웃돌게 두어 '구속 조건'이 되지 않게 한다.
GEMINI_MAX_OUTPUT_TOKENS = 4096


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _query_chatgpt(query: str) -> dict[str, Any]:
    """ChatGPT 호출.

    프로덕션 설정은 web_search를 강제한다. chat.completions 경로는 기존 측정 호환과
    로컬 개발만을 위한 것으로, Settings가 production+False 조합을 부팅 단계에서 거부한다.
    """
    if settings.OPENAI_CHATGPT_USE_WEB_SEARCH:
        return await _query_chatgpt_with_search_result(query)
    await _record_sov_provider_call()
    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_QUERY,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_SOV},
            {"role": "user", "content": query},
        ],
        temperature=0.7,
        max_tokens=800,
    )
    usage = _field(response, "usage")
    return {
        "text": response.choices[0].message.content or "",
        "source_urls": [],
        "answer_model": _field(response, "model"),
        # 이 경로는 도구를 주지 않는다 — 검색 0회가 사실이다(None이 아니다).
        "search_calls": 0,
        "input_tokens": _field(usage, "prompt_tokens"),
        "output_tokens": _field(usage, "completion_tokens"),
        "measurement_method": "OPENAI_CHAT_COMPLETIONS",
    }


async def _query_chatgpt_with_search(query: str) -> str:
    """진단 코드와 기존 호출부를 위한 text-only 호환 래퍼."""
    return str((await _query_chatgpt_with_search_result(query))["text"])


async def _query_chatgpt_with_search_result(query: str) -> dict[str, Any]:
    """OpenAI Responses web search의 답변과 실제 인용 URL을 함께 보존한다."""
    await _record_sov_provider_call()
    try:
        response = await openai_client.responses.create(
            model=settings.OPENAI_MODEL_QUERY,
            tools=[{"type": "web_search"}],
            # 도구는 제공하되 강제하지 않는다 (측정 정책 v2). 매 요청 검색을 강제하면
            # 지역 병원 디렉터리를 긁어와 나열하게 되어, 환자가 실제로 받는 답변보다
            # 구조적으로 병원명이 많이 등장한다. 모델이 검색을 쓸지 고르는 것까지가
            # 측정 대상이다.
            tool_choice=OPENAI_SEARCH_TOOL_CHOICE,
            # **지시문은 지시문 자리로 보낸다.** 이전에는 `input`에 이어붙였는데,
            # 리포트는 그것을 "시스템 지시문"이라고 인쇄했다 — 역할이 다르면 같은
            # 문자열이라도 모델 동작이 달라지므로, 공개한 조건으로 재현이 안 됐다.
            instructions=SYSTEM_PROMPT_SOV,
            input=query,
        )
    except AttributeError:
        # SDK 버전이 responses API를 지원하지 않으면 chat.completions로 폴백
        # 운영자가 OPENAI_CHATGPT_USE_WEB_SEARCH=true로 켰지만 SDK 미지원이라 빈 결과로
        # 분리되는 게 맞으므로 — 빈 문자열 반환해 FAILED 라벨로 흐르게 함.
        logger.warning("openai SDK has no .responses; falling through to FAILED.")
        return {
            "text": "",
            "source_urls": [],
            "measurement_method": "OPENAI_RESPONSES_WEB_SEARCH",
        }
    output_text = getattr(response, "output_text", None)
    text = output_text if isinstance(output_text, str) else ""
    if not text:
        for item in _field(response, "output", []) or []:
            for content in _field(item, "content", []) or []:
                candidate = _field(content, "text")
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate
                    break
            if text:
                break
    input_tokens, output_tokens = _extract_openai_usage(response)
    return {
        "text": text,
        "source_urls": _extract_openai_source_urls(response),
        "answer_model": _field(response, "model"),
        "search_calls": _extract_openai_search_calls(response),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "measurement_method": "OPENAI_RESPONSES_WEB_SEARCH",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _query_gemini(query: str) -> str:
    """진단 코드와 기존 호출부를 위한 text-only 호환 래퍼."""
    return str((await _query_gemini_result(query))["text"])


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _query_gemini_result(query: str) -> dict[str, Any]:
    client = _get_gemini_client()
    if not client:
        return {
            "text": "",
            "source_urls": [],
            "measurement_method": "GEMINI_GOOGLE_SEARCH",
        }
    await _record_sov_provider_call()
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.models.generate_content,
            model=settings.GEMINI_MODEL,
            contents=query,
            config=genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                # OpenAI 경로와 **같은 문자열을 같은 역할로** 보낸다. 한쪽만 지시문을
                # 질문에 이어붙이면 "ChatGPT n% vs Gemini m%"가 플랫폼 차이가 아니라
                # 우리 호출 방식의 차이가 된다 (2026-07-29 비대칭 회귀와 같은 종류).
                system_instruction=SYSTEM_PROMPT_SOV,
            ),
        ),
        timeout=GEMINI_TIMEOUT_SECONDS,
    )
    input_tokens, output_tokens = _extract_gemini_usage(response)
    return {
        "text": response.text or "",
        "source_urls": _extract_gemini_source_urls(response),
        "answer_model": _field(response, "model_version"),
        "search_calls": _extract_gemini_search_calls(response),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "measurement_method": "GEMINI_GOOGLE_SEARCH",
    }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_source_urls(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        url = str(value or "").strip()
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url not in normalized:
            normalized.append(url)
    return normalized


def _extract_openai_source_urls(response: Any) -> list[str]:
    urls: list[Any] = []
    for item in _field(response, "output", []) or []:
        for content in _field(item, "content", []) or []:
            for annotation in _field(content, "annotations", []) or []:
                urls.append(_field(annotation, "url"))
                citation = _field(annotation, "url_citation")
                if citation:
                    urls.append(_field(citation, "url"))
        action = _field(item, "action")
        for source in _field(action, "sources", []) or []:
            urls.append(_field(source, "url"))
    return _normalize_source_urls(urls)


def _extract_gemini_source_urls(response: Any) -> list[str]:
    urls: list[Any] = []
    for candidate in _field(response, "candidates", []) or []:
        metadata = _field(candidate, "grounding_metadata")
        for chunk in _field(metadata, "grounding_chunks", []) or []:
            urls.append(_field(_field(chunk, "web"), "uri"))
    return _normalize_source_urls(urls)


# ── 측정 메타데이터 추출.
#
# 검색이 실제로 돌았는지는 **측정 결과를 해석하는 데 필수**다. `tool_choice=auto`로
# 바꾼 뒤 "그래도 매번 검색이 도니까 숫자가 높다"는 설명이 나왔는데, 이 값을 저장하지
# 않아 확인할 방법이 없었다. 설명할 수 없는 숫자는 팔 수 없다.
#
# 실제 응답 모델(`answer_model`)도 같은 이유다 — 리포트는 "요청한 모델"이 아니라
# 실제로 답한 모델을 공개해야 재현성 계약이 성립한다.


def _extract_openai_search_calls(response: Any) -> int:
    """모델이 실제로 호출한 web_search 횟수. 도구를 강제하지 않으므로 0일 수 있다."""
    return sum(
        1
        for item in (_field(response, "output", []) or [])
        if str(_field(item, "type", "")).startswith("web_search")
    )


def _extract_openai_usage(response: Any) -> tuple[int | None, int | None]:
    usage = _field(response, "usage")
    return _field(usage, "input_tokens"), _field(usage, "output_tokens")


def _extract_gemini_search_calls(response: Any) -> int:
    """Gemini가 실제로 발행한 검색 질의 수. grounding이 안 걸리면 0이다."""
    calls = 0
    for candidate in _field(response, "candidates", []) or []:
        metadata = _field(candidate, "grounding_metadata")
        calls += len(_field(metadata, "web_search_queries", []) or [])
    return calls


def _extract_gemini_usage(response: Any) -> tuple[int | None, int | None]:
    usage = _field(response, "usage_metadata")
    return _field(usage, "prompt_token_count"), _field(usage, "candidates_token_count")


def _normalize_for_prefilter(text: str) -> str:
    """사전 필터 비교용 정규화 — 공백·특수문자를 제거해 표기 변형(띄어쓰기 등)에 강건하게.

    예: "장편한 외과" ↔ "장편한외과" 가 사전 필터에서 어긋나 실제 언급을 놓치던 문제 완화.
    """
    return re.sub(r"[\s\W]+", "", text or "", flags=re.UNICODE)


def prefilter_key(hospital_name: str) -> str:
    """사전 필터 비교 키 — 자사·경쟁사에 **동일하게** 적용한다.

    접미사(의원/병원/클리닉)를 떼고 남은 핵심을 키로 쓰되, 핵심이 3글자 미만이면
    접미사를 포함한 원래 이름을 키로 쓴다. 앞 2글자 같은 느슨한 키는 서로 다른 기관을
    같은 병원으로 오인시키므로 쓰지 않는다.

    자사에만 엄격한 키를, 경쟁사에 느슨한 키를 쓰면 경쟁사 언급이 구조적으로 부풀려져
    원장 보고서의 "우리 병원 vs 경쟁 병원" 비교가 무효가 된다. 그래서 한 함수로 묶는다.
    """
    normalized = _normalize_for_prefilter(hospital_name)
    core = re.sub(r"(의원|병원|클리닉)$", "", normalized)
    return core if len(core) >= 3 else normalized


def _not_matched(verdict: str = VERDICT_NOT_MATCHED) -> dict:
    return {
        "verdict": verdict,
        "is_mentioned": False if verdict == VERDICT_NOT_MATCHED else None,
        "matched_text": None,
        "mention_rank": None,
        "sentiment": None,
        "mention_context": None,
    }


def _corroborates(hospital_name: str, matched_text: str | None, response_text: str) -> bool:
    """판정기가 MATCHED의 근거로 든 표현이 실제 언급 증거인가.

    사전 필터는 접미사를 뗀 핵심의 substring 일치라 느슨하다 — "행복한의원"의 핵심
    "행복한"은 "행복한 진료를 위해" 같은 평범한 문장에도 걸린다. 필터를 통과한 뒤
    판정기가 그 조각을 근거로 MATCHED를 주면 오탐이 그대로 언급으로 계상된다.

    승인 기준은 동일성 규칙(_IDENTITY_RULE)이 명시한 그대로다: 병원명 전체, 또는
    **법적 접미사(의원/병원/클리닉)만 뗀 상호**. AI 답변은 "군자성모정형외과의원"을
    "군자성모정형외과"로 부르는 게 보통이라, 전체 명칭만 요구하면 정상 언급이 대량으로
    보류에 떨어진다 — 실측에서 플랫폼당 3~4건이 그렇게 빠져 하한 미달까지 갔다.

    인용이 핵심(접두 상호)에도 못 미치는 조각이면 승인하지 않는다. 미언급으로
    내리지도 않는다 — 둘 다 틀릴 수 있는 자리에서는 '확정 불가'가 사실이다.
    """
    quoted = _normalize_for_prefilter(matched_text or "")
    if not quoted:
        return False
    # 지어낸 인용은 근거가 아니다.
    if quoted not in _normalize_for_prefilter(response_text):
        return False
    normalized_name = _normalize_for_prefilter(hospital_name)
    if normalized_name in quoted:
        return True
    # 접미사만 뗀 상호와의 **동등** 비교. 포함(in)으로 풀면 "행복한의원"의 핵심
    # "행복한"이 "행복한 진료" 같은 인용에 다시 걸린다 — 판정 프롬프트가 인용을
    # "언급 표현 그대로"로 요구하므로, 약칭 언급이면 인용 자체가 핵심과 일치한다.
    # 핵심이 3글자 미만이면 접미사 포함 전체를 요구한다 (prefilter_key와 같은 하한).
    core = re.sub(r"(의원|병원|클리닉)$", "", normalized_name)
    return len(core) >= 3 and quoted == core


async def _parse_mention(hospital_name: str, response_text: str, region: str = "") -> dict:
    if not response_text.strip():
        return _not_matched()
    # 빠른 사전 필터 — 경쟁사 판정과 동일한 키를 쓴다(prefilter_key).
    normalized_response = _normalize_for_prefilter(response_text)
    prefilter_name = prefilter_key(hospital_name)
    if prefilter_name and prefilter_name not in normalized_response:
        logger.debug("prefilter skip (mention): hospital=%s", hospital_name)
        return _not_matched()

    await _record_sov_provider_call()
    result = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_PARSE,
        messages=[
            {
                "role": "user",
                "content": PARSE_PROMPT.format(
                    response=response_text[:3000],
                    hospital_name=hospital_name,
                    region=region or "미상",
                ),
            }
        ],
        temperature=0,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(result.choices[0].message.content or "{}")
    except Exception as exc:
        raise ValueError("mention_parse_failed") from exc
    if not isinstance(parsed, dict) or parsed.get("verdict") not in _VERDICTS:
        raise ValueError("mention_parse_failed")

    verdict = parsed["verdict"]
    matched_text = parsed.get("matched_text")
    if verdict == VERDICT_MATCHED and not _corroborates(
        hospital_name, matched_text, response_text
    ):
        # 판정기가 근거를 대지 못한 MATCHED는 승격시키지 않는다. 미언급으로도
        # 내리지 않는다 — 둘 다 틀릴 수 있는 자리에서는 '확정 불가'가 사실이다.
        logger.debug(
            "mention verdict downgraded to AMBIGUOUS: hospital=%s quote=%r",
            hospital_name,
            matched_text,
        )
        verdict = VERDICT_AMBIGUOUS

    return {
        "verdict": verdict,
        # AMBIGUOUS는 참도 거짓도 아니다. None으로 두어 집계가 분모·분자 양쪽에서
        # 빼도록 강제한다 — False로 접으면 조용히 하향 편향이 된다.
        "is_mentioned": (
            True if verdict == VERDICT_MATCHED
            else (False if verdict == VERDICT_NOT_MATCHED else None)
        ),
        "matched_text": matched_text,
        "mention_rank": parsed.get("mention_rank"),
        "sentiment": parsed.get("sentiment"),
        "mention_context": parsed.get("mention_context"),
    }


async def _parse_competitors(competitors: list[str], response_text: str) -> list[dict]:
    if not competitors or not response_text.strip():
        return []
    # 빠른 사전 필터 — 자사 판정과 **동일한** 키를 쓴다(prefilter_key).
    # 과거에는 앞 2글자만 맞으면 통과시켜 경쟁사 언급이 구조적으로 부풀려졌다.
    normalized_response = _normalize_for_prefilter(response_text)
    if not any(
        (key := prefilter_key(c)) and key in normalized_response for c in competitors
    ):
        logger.debug("prefilter skip (competitors): count=%d", len(competitors))
        return [{"name": c, "is_mentioned": False, "mention_rank": None} for c in competitors]

    await _record_sov_provider_call()
    result = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_PARSE,
        messages=[
            {
                "role": "user",
                "content": COMPETITOR_PARSE_PROMPT.format(
                    response=response_text[:3000],
                    competitor_names="\n".join(f"- {c}" for c in competitors),
                ),
            }
        ],
        temperature=0,
        max_tokens=500,
        response_format={"type": "json_object"},
    )
    # 판정기 장애를 "미언급"으로 삼키지 않는다. 자사 판정(_parse_mention)은 파싱 실패 시
    # ValueError를 던져 측정이 FAILED로 분모에서 빠지는데, 경쟁사만 조용히 전부 False를
    # 돌려주면 **같은 장애가 자사는 분모 제외, 경쟁사는 미언급으로 집계**된다. 방향이
    # 한쪽으로 몰린 편향이므로 자사와 동일하게 실패로 올린다.
    #
    # 사전 필터에서 걸러 all-false를 돌려주는 경로(위)는 실패가 아니라 판정 결과다.
    try:
        parsed = json.loads(result.choices[0].message.content or "{}")
    except Exception as exc:
        raise ValueError("competitor_parse_failed") from exc
    if isinstance(parsed, dict) and "competitors" in parsed:
        parsed = parsed["competitors"]
    if not isinstance(parsed, list):
        raise ValueError("competitor_parse_failed")
    for item in parsed:
        if not isinstance(item, dict) or not isinstance(item.get("is_mentioned"), bool):
            raise ValueError("competitor_parse_failed")
    return parsed


async def run_single_query(
    hospital_name: str,
    query_text: str,
    platform: str,
    repeat_count: int,
    competitors: list[str] | None = None,
    pool: str = POOL_SOV,
    region: str = "",
) -> list[dict]:
    """`pool`은 동시성 풀만 고르고 측정 조건은 바꾸지 않는다.

    무료 진단과 유료 측정이 **같은 모델·같은 프롬프트·같은 판정 기준**을 써야
    무료에서 본 숫자와 첫 유료 리포트가 어긋나지 않는다(PRD §7-4). 여기서 갈리는 것은
    "동시에 몇 개를 던지느냐"뿐이다.

    `region`도 그 조건의 일부다 — 무료 진단은 지역을 판정기에 넘기는데 유료가 넘기지
    않으면, 같은 동명 기관 답변이 무료에서는 확정되고 유료에서는 보류가 된다.
    """
    query_fn = _query_chatgpt if platform == "chatgpt" else _query_gemini_result

    async def single():
        # 이 측정에서 나가는 실제 호출을 어느 예산으로 셀지 고정한다. 무료 진단이
        # 유료 측정 예산에 섞이면 상한 판단이 무너진다.
        _provider_cost_category.set(pool)
        async with _get_semaphore(pool):
            try:
                provider_result = await query_fn(query_text)
            except Exception as e:
                # 쿼리 자체 실패 → raw="" 로 FAILED 처리.
                logger.error(f"Query failed: {e}")
                return {
                    "is_mentioned": False,
                    "mention_rank": None,
                    "sentiment": None,
                    "mention_context": None,
                    "raw_response": "",
                    "competitor_mentions": None,
                    "source_urls": [],
                    "measurement_status": "FAILED",
                    "failure_reason": f"provider_query_failed:{type(e).__name__}",
                }
            if isinstance(provider_result, str):
                provider_result = {"text": provider_result, "source_urls": []}
            raw = str(provider_result.get("text") or "")
            source_urls = _normalize_source_urls(provider_result.get("source_urls") or [])
            measurement_method = provider_result.get("measurement_method")
            if not raw.strip():
                return {
                    "is_mentioned": False,
                    "mention_rank": None,
                    "sentiment": None,
                    "mention_context": None,
                    "raw_response": "",
                    "competitor_mentions": None,
                    "source_urls": source_urls,
                    "measurement_method": measurement_method,
                    "measurement_status": "FAILED",
                    "failure_reason": "empty_raw_response",
                }
            try:
                parsed = await _parse_mention(hospital_name, raw, region)
                comp_mentions = (
                    await _parse_competitors(competitors or [], raw) if competitors else []
                )
                return {
                    **parsed,
                    "raw_response": raw,
                    "competitor_mentions": comp_mentions or None,
                    "source_urls": source_urls,
                    "answer_model": provider_result.get("answer_model"),
                    "search_calls": provider_result.get("search_calls"),
                    "input_tokens": provider_result.get("input_tokens"),
                    "output_tokens": provider_result.get("output_tokens"),
                    "measurement_method": measurement_method,
                    "measurement_status": "SUCCESS",
                    "failure_reason": None,
                }
            except Exception as e:
                # 응답 수신과 언급 판정 성공은 별개다. 파싱 실패를 미언급 0%로 넣지 않는다.
                logger.warning(f"Parse failed (query ok): {e}")
                return {
                    "is_mentioned": False,
                    "mention_rank": None,
                    "sentiment": None,
                    "mention_context": None,
                    "raw_response": raw,
                    "competitor_mentions": None,
                    "source_urls": source_urls,
                    "measurement_method": measurement_method,
                    "measurement_status": "FAILED",
                    "failure_reason": "mention_parse_failed",
                }

    return list(await asyncio.gather(*[single() for _ in range(repeat_count)]))


async def fetch_answer(
    query_text: str,
    platform: str,
    *,
    pool: str = POOL_SOV,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """공급자 답변만 가져온다 — **판정은 하지 않는다.**

    `requested_model`은 접수 시점에 고정한 모델이다. 넘기지 않으면 실행 시점의 전역
    설정을 쓰는데, API 배포와 워커 배포 사이에 모델이 바뀌면 **캐시 키·리포트 표기와
    실제 호출 모델이 어긋난다** — M2 답변이 M1 캐시에 저장되고 리포트는 M1이라고 적힌다.
    불일치가 감지되면 호출하지 않고 실패로 돌려준다: 잘못된 모델로 잰 숫자를 파느니
    한 건 실패하는 편이 낫다.

    `run_single_query`는 답변과 판정을 한 덩어리로 묶는데, 질의 단위 공유 캐시는
    둘을 갈라야 성립한다: 답변은 병원과 무관해 재사용할 수 있지만 판정은 병원마다
    다시 해야 하기 때문이다(설계 §2-6).

    실패는 예외가 아니라 `measurement_status='FAILED'`인 dict로 돌려준다 — 호출부가
    측정 1건의 실패와 진단 전체의 실패를 구분해야 한다.
    """
    if requested_model:
        active = settings.OPENAI_MODEL_QUERY if platform == "chatgpt" else settings.GEMINI_MODEL
        if requested_model != active:
            logger.error(
                "pinned model drift: requested=%s active=%s platform=%s",
                requested_model, active, platform,
            )
            return {
                "text": "",
                "source_urls": [],
                "measurement_status": "FAILED",
                "failure_reason": f"pinned_model_drift:{requested_model}!={active}",
            }

    query_fn = _query_chatgpt if platform == "chatgpt" else _query_gemini_result
    # 무료 진단 경로 — 실제 호출을 leadgen 예산으로 센다(위 measure 경로와 같은 규약).
    _provider_cost_category.set(pool)
    async with _get_semaphore(pool):
        try:
            provider_result = await query_fn(query_text)
        except Exception as exc:  # noqa: BLE001 — 측정 1건의 실패는 진단을 멈추지 않는다.
            logger.error("Query failed (%s): %s", platform, exc)
            return {
                "text": "",
                "source_urls": [],
                "measurement_status": "FAILED",
                "failure_reason": f"provider_query_failed:{type(exc).__name__}",
            }

    if isinstance(provider_result, str):
        provider_result = {"text": provider_result, "source_urls": []}
    raw = str(provider_result.get("text") or "")
    if not raw.strip():
        return {
            "text": "",
            "source_urls": _normalize_source_urls(provider_result.get("source_urls") or []),
            "measurement_status": "FAILED",
            "failure_reason": "empty_raw_response",
        }
    return {
        "text": raw,
        "source_urls": _normalize_source_urls(provider_result.get("source_urls") or []),
        "answer_model": provider_result.get("answer_model"),
        # 검색이 실제로 돌았는지는 이 숫자를 해석하는 데 필수다 — 없으면
        # "검색 때문에 높다"는 설명을 확인할 수도 반박할 수도 없다.
        "search_calls": provider_result.get("search_calls"),
        "input_tokens": provider_result.get("input_tokens"),
        "output_tokens": provider_result.get("output_tokens"),
        "measurement_method": provider_result.get("measurement_method"),
        "measurement_status": "SUCCESS",
        "failure_reason": None,
    }


async def judge_mention(hospital_name: str, response_text: str, region: str = "") -> dict:
    """이 답변이 그 병원을 언급했는가.

    캐시된 답변에도 **반드시 다시 실행한다** — 답변은 병원과 무관하지만 판정은 아니다.

    `region`은 동명 기관을 가르는 유일한 단서다. 넘기지 않으면 판정기는 "서울내과"가
    이 병원인지 다른 동네 같은 이름인지 알 방법이 없고, 그 불확실성이 MATCHED로 접힌다.
    """
    return await _parse_mention(hospital_name, response_text, region)


def calculate_sov(
    results: list[dict], *, intents: frozenset[str] | None = MENTION_RATE_INTENTS
) -> float | None:
    """AI 답변 언급률(%) — 측정 실패와 '이길 수 없는 질문'은 분모에서 제외.

    - measurement_status == "FAILED" → 분모 제외 (실패가 SoV를 인공적으로 낮추는 것을 방지)
    - measurement_status 미존재 + raw_response 비어있음 → 분모 제외 (네트워크 실패 추정)
    - query_intent가 INFO → 분모 제외 (지역 없는 의학 설명 질문. AI가 특정 의원 이름을
      댈 이유가 없어 병원이 무엇을 하든 0으로 고정이다. 전체의 22%를 차지해 우리 성과를
      그만큼 깎아서 보고하고 있었다.) intents=None을 주면 유형을 가리지 않는다.
    - 그 외는 SUCCESS로 간주

    반환 계약: 성공 측정이 1건 이상이면 언급률(float), 성공 레코드가 0건이면 None.
    None은 '측정 안 됨'을 뜻하며 '실제 0% 언급'(0.0)과 구분된다 — 허위 0%가 PDF/Slack
    원장 보고에 들어가지 않도록 호출부가 None을 명시적으로 표기해야 한다.
    """
    successful = successful_records(results, intents=intents)
    if not successful:
        return None
    return round(sum(1 for r in successful if r.get("is_mentioned")) / len(successful) * 100, 2)


def successful_records(results: list[dict], *, intents: frozenset[str] | None = None) -> list[dict]:
    """실패와 판정 보류를 걸러내고, 요청한 질문 유형만 남긴다.

    intents=None이면 유형을 가리지 않는다(운영 진단·전체 집계용).
    유형이 없는 레코드는 LOCAL로 본다 — classify_query_intent와 같은 fail-open이다.

    AMBIGUOUS는 분모에서 뺀다(PRD F3-7). 확정하지 못한 판정을 미언급으로 세면 하향
    편향, 언급으로 세면 상향 편향이다 — 어느 쪽도 사실이 아니므로 세지 않고 따로 센다.
    """
    successful: list[dict] = []
    for r in results:
        status = r.get("measurement_status")
        if status == "FAILED":
            continue
        if status is None and "raw_response" in r and not (r.get("raw_response") or "").strip():
            continue
        # 판정 보류만 뺀다. `is_mentioned` 키가 아예 없는 레코드(집계용으로 조립된
        # 요약 dict 등)까지 빼면 분모가 조용히 줄어들므로, **키가 있는데 None인**
        # 경우만 보류로 본다.
        if r.get("verdict") == VERDICT_AMBIGUOUS or (
            "is_mentioned" in r and r["is_mentioned"] is None
        ):
            continue
        if intents is not None:
            intent = r.get("query_intent") or QUERY_INTENT_LOCAL
            if intent not in intents:
                continue
        successful.append(r)
    return successful


def segment_mention_rates(results: list[dict]) -> dict[str, dict]:
    """유형별 언급률 — 헤드라인 숫자가 어디서 나왔는지 리포트가 보여줄 수 있게 한다.

    INFO 구간의 언급률은 헤드라인에서 빠지지만 사라지면 안 된다. 병원이 이길 수 없는
    질문이라는 사실 자체가 원장에게 설명되어야 "왜 이 숫자가 올랐나"가 통한다.
    """
    out: dict[str, dict] = {}
    for intent in (QUERY_INTENT_LOCAL, QUERY_INTENT_INFO):
        rows = successful_records(results, intents=frozenset({intent}))
        out[intent] = {
            "measured": len(rows),
            "mentioned": sum(1 for r in rows if r.get("is_mentioned")),
            "mention_rate": (
                round(sum(1 for r in rows if r.get("is_mentioned")) / len(rows) * 100, 2)
                if rows
                else None
            ),
        }
    return out

"""AI 답변 언급률 엔진 — 환자 질문 생성·발송·파싱·계산"""

import asyncio
import json
import logging
import re
import threading
from itertools import product
from typing import Any
from urllib.parse import urlparse

from google import genai as google_genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)

_sem_lock = threading.Lock()
_api_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore bound to the current event loop.
    Thread-safe: uses a lock for creation. Recreates if the loop changed.
    """
    global _api_semaphore, _semaphore_loop
    current_loop = asyncio.get_running_loop()
    if _api_semaphore is None or _semaphore_loop is not current_loop:
        with _sem_lock:
            if _api_semaphore is None or _semaphore_loop is not current_loop:
                _api_semaphore = asyncio.Semaphore(SOV_PROVIDER_CONCURRENCY)
                _semaphore_loop = current_loop
    return _api_semaphore


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


QUERY_TEMPLATES = [
    # 추천형
    "{region} {keyword} 잘 보는 병원 추천해줘",
    "{region} {specialty} 어디가 좋아",
    "{sub_region} {keyword} 잘하는 곳",
    "{region} {specialty} 전문의 추천",
    "{keyword} 수술 {region} 어느 병원이 좋아?",
    "{region} {keyword} 치료 잘하는 병원",
    # 증상·탐색형
    "{keyword} 증상 {region}에서 치료 잘하는 곳",
    "{keyword} 있는데 {region} 어느 병원 가야 해?",
    "{keyword} 초기 증상이 뭔지 알려줘",
    "{keyword} 치료하려면 어떤 전문의한테 가야 해?",
    "{region} {keyword} 빨리 낫는 병원",
    # 비교형
    "{region} {specialty} 병원 어디가 좋은지 비교해줘",
    "{region} {keyword} 병원 후기 좋은 곳",
    "{sub_region} {specialty} 잘한다고 소문난 병원",
    # 비용·정보형
    "{keyword} 치료 비용이 얼마나 드는지 알려줘",
    "{keyword} 수술 후 회복 기간 얼마나 돼?",
    "{keyword} 비수술 치료 가능한 병원 {region}",
    "{region} {specialty} 비용 어느 정도야?",
]

# 자사·경쟁사 판정에 동일하게 들어가는 동일성 기준. 두 프롬프트가 서로 다른 기준을 쓰면
# "우리 병원 vs 경쟁 병원" 비교가 사과와 오렌지가 된다 — 원장 보고서의 핵심 지표이므로
# 문구를 한 곳에서 정의해 양쪽에 주입한다.
_IDENTITY_RULE = """\
띄어쓰기·의원/병원 접미사 차이처럼 동일 기관임이 명확한 표기 변형만 인정한다.
흔한 앞글자 2~3자가 같거나 다른 지역의 동명 기관인 것만으로는 동일 병원으로 간주하지 않는다."""

PARSE_PROMPT = f"""\
다음 AI 답변에서 "{{hospital_name}}"이 언급되었는지 분석하라.
{_IDENTITY_RULE}

[답변]
{{response}}

반드시 아래 JSON만 출력:
{{{{"is_mentioned": true/false, "mention_rank": null 또는 정수, "sentiment": "positive"/"neutral"/"negative"/null, "mention_context": "언급 문장 또는 null"}}}}"""

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

    queries = set()
    main_region = region[0] if region else ""
    sub_region = region[1] if len(region) > 1 else main_region
    for template, keyword, specialty in product(QUERY_TEMPLATES, keywords, specialties):
        q = template.format(
            region=main_region, sub_region=sub_region, keyword=keyword, specialty=specialty
        )
        queries.add(q)
    return list(queries)


# 두 플랫폼에 **같은 지시문을 같은 위치로** 준다.
#
# 2026-07-29 이전에는 ChatGPT에만 이 지시문이 붙고 Gemini는 질문만 받았다. 그래서
# "ChatGPT 32% vs Gemini 8%" 같은 숫자가 나와도 그건 플랫폼 차이가 아니라 우리가 두
# 모델을 다르게 대우한 결과였다 — 플랫폼 비교가 성립하지 않았다.
# 이름을 CHATGPT에서 SOV로 바꾼 것은 "이건 한 플랫폼 전용이 아니다"를 코드에 못 박기 위함이다.
SYSTEM_PROMPT_SOV = (
    "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."
)


def build_sov_prompt(query: str) -> str:
    """플랫폼 공통 질의문. 양쪽이 반드시 이 함수를 거쳐야 비대칭이 재발하지 않는다."""
    return f"{SYSTEM_PROMPT_SOV}\n\n질문: {query}"


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
    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_QUERY,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_SOV},
            {"role": "user", "content": query},
        ],
        temperature=0.7,
        max_tokens=800,
    )
    return {
        "text": response.choices[0].message.content or "",
        "source_urls": [],
        "measurement_method": "OPENAI_CHAT_COMPLETIONS",
    }


async def _query_chatgpt_with_search(query: str) -> str:
    """진단 코드와 기존 호출부를 위한 text-only 호환 래퍼."""
    return str((await _query_chatgpt_with_search_result(query))["text"])


async def _query_chatgpt_with_search_result(query: str) -> dict[str, Any]:
    """OpenAI Responses web search의 답변과 실제 인용 URL을 함께 보존한다."""
    try:
        response = await openai_client.responses.create(
            model=settings.OPENAI_MODEL_QUERY,
            tools=[{"type": "web_search"}],
            # 도구를 단순 제공만 하면 모델이 검색 없이 답할 수 있다. SoV 계약은 실제
            # 웹 검색 노출 측정이므로 매 요청에서 web_search 호출을 강제한다.
            tool_choice="required",
            input=build_sov_prompt(query),
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
    return {
        "text": text,
        "source_urls": _extract_openai_source_urls(response),
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
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.models.generate_content,
            model=settings.GEMINI_MODEL,
            contents=build_sov_prompt(query),
            config=genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        ),
        timeout=GEMINI_TIMEOUT_SECONDS,
    )
    return {
        "text": response.text or "",
        "source_urls": _extract_gemini_source_urls(response),
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


async def _parse_mention(hospital_name: str, response_text: str) -> dict:
    if not response_text.strip():
        return {
            "is_mentioned": False,
            "mention_rank": None,
            "sentiment": None,
            "mention_context": None,
        }
    # 빠른 사전 필터 — 경쟁사 판정과 동일한 키를 쓴다(prefilter_key).
    normalized_response = _normalize_for_prefilter(response_text)
    prefilter_name = prefilter_key(hospital_name)
    if prefilter_name and prefilter_name not in normalized_response:
        logger.debug("prefilter skip (mention): hospital=%s", hospital_name)
        return {
            "is_mentioned": False,
            "mention_rank": None,
            "sentiment": None,
            "mention_context": None,
        }

    result = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_PARSE,
        messages=[
            {
                "role": "user",
                "content": PARSE_PROMPT.format(
                    response=response_text[:3000], hospital_name=hospital_name
                ),
            }
        ],
        temperature=0,
        max_tokens=200,
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(result.choices[0].message.content or "{}")
    except Exception as exc:
        raise ValueError("mention_parse_failed") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("is_mentioned"), bool):
        raise ValueError("mention_parse_failed")
    return parsed


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
) -> list[dict]:
    query_fn = _query_chatgpt if platform == "chatgpt" else _query_gemini_result

    async def single():
        async with _get_semaphore():
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
                parsed = await _parse_mention(hospital_name, raw)
                comp_mentions = (
                    await _parse_competitors(competitors or [], raw) if competitors else []
                )
                return {
                    **parsed,
                    "raw_response": raw,
                    "competitor_mentions": comp_mentions or None,
                    "source_urls": source_urls,
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


def calculate_sov(results: list[dict]) -> float | None:
    """AI 답변 언급률(%) — 측정 실패는 분모에서 제외.

    - measurement_status == "FAILED" → 분모 제외 (실패가 SoV를 인공적으로 낮추는 것을 방지)
    - measurement_status 미존재 + raw_response 비어있음 → 분모 제외 (네트워크 실패 추정)
    - 그 외는 SUCCESS로 간주

    반환 계약: 성공 측정이 1건 이상이면 언급률(float), 성공 레코드가 0건이면 None.
    None은 '측정 안 됨'을 뜻하며 '실제 0% 언급'(0.0)과 구분된다 — 허위 0%가 PDF/Slack
    원장 보고에 들어가지 않도록 호출부가 None을 명시적으로 표기해야 한다.
    """
    successful: list[dict] = []
    for r in results:
        status = r.get("measurement_status")
        if status == "FAILED":
            continue
        if status is None and "raw_response" in r and not (r.get("raw_response") or "").strip():
            continue
        successful.append(r)
    if not successful:
        return None
    return round(sum(1 for r in successful if r.get("is_mentioned")) / len(successful) * 100, 2)

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
                _api_semaphore = asyncio.Semaphore(5)
                _semaphore_loop = current_loop
    return _api_semaphore


openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0)
_gemini_client: google_genai.Client | None = None


def _get_gemini_client() -> google_genai.Client | None:
    global _gemini_client
    if settings.GEMINI_API_KEY and _gemini_client is None:
        _gemini_client = google_genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options={"timeout": 30000},  # 30s in milliseconds
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

PARSE_PROMPT = """\
다음 AI 답변에서 "{hospital_name}"이 언급되었는지 분석하라.
띄어쓰기·의원/병원 접미사 차이처럼 동일 기관임이 명확한 표기 변형만 인정한다.
흔한 앞글자 2~3자가 같거나 다른 지역의 동명 기관인 것만으로는 동일 병원으로 간주하지 않는다.

[답변]
{response}

반드시 아래 JSON만 출력:
{{"is_mentioned": true/false, "mention_rank": null 또는 정수, "sentiment": "positive"/"neutral"/"negative"/null, "mention_context": "언급 문장 또는 null"}}"""

COMPETITOR_PARSE_PROMPT = """\
다음 AI 답변에서 아래 병원들이 각각 언급되었는지 분석하라.
병원명 축약형·변형도 언급으로 인정한다 (앞 2~3글자 일치 시 동일 병원).

[분석 대상 병원 목록]
{competitor_names}

[답변]
{response}

반드시 아래 JSON 객체만 출력:
{{"competitors": [{{"name": "병원명", "is_mentioned": true/false, "mention_rank": null 또는 정수}}]}}"""


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


SYSTEM_PROMPT_CHATGPT = (
    "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."
)


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
            {"role": "system", "content": SYSTEM_PROMPT_CHATGPT},
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
            input=f"{SYSTEM_PROMPT_CHATGPT}\n\n질문: {query}",
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
            contents=query,
            config=genai_types.GenerateContentConfig(
                temperature=1.0,
                max_output_tokens=800,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        ),
        timeout=30.0,
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


async def _parse_mention(hospital_name: str, response_text: str) -> dict:
    if not response_text.strip():
        return {
            "is_mentioned": False,
            "mention_rank": None,
            "sentiment": None,
            "mention_context": None,
        }
    # 빠른 사전 필터 — 공백/기호만 정규화한 병원명 핵심 3글자를 사용한다.
    # 2글자 접두사만으로 동명·유사 기관을 같은 병원으로 오인하지 않도록 한다.
    normalized_name = _normalize_for_prefilter(hospital_name)
    normalized_response = _normalize_for_prefilter(response_text)
    core_name = re.sub(r"(의원|병원|클리닉)$", "", normalized_name)
    prefilter_name = core_name if len(core_name) >= 3 else normalized_name
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
    # 빠른 사전 필터: 정규화(공백/특수문자 제거) 후 어떤 경쟁사의 앞 2글자도 없으면 스킵
    normalized_response = _normalize_for_prefilter(response_text)
    if not any(
        (norm := _normalize_for_prefilter(c)[:2]) and norm in normalized_response
        for c in competitors
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
    try:
        parsed = json.loads(result.choices[0].message.content or "{}")
        if isinstance(parsed, dict) and "competitors" in parsed:
            parsed = parsed["competitors"]
        if isinstance(parsed, list):
            return parsed
        return [{"name": c, "is_mentioned": False, "mention_rank": None} for c in competitors]
    except Exception:
        return [{"name": c, "is_mentioned": False, "mention_rank": None} for c in competitors]


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

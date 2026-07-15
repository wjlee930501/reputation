"""AI 답변 언급률 엔진 — 환자 질문 생성·발송·파싱·계산"""

import asyncio
import json
import logging
import re
import threading
from itertools import product

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
병원명 축약형·변형도 언급으로 인정한다.
예: "장편한외과" = "장편한외과의원" = "장편한 외과" = "장편한" 등 — 앞 2~3글자가 일치하면 동일 병원으로 간주한다.

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
async def _query_chatgpt(query: str) -> str:
    """ChatGPT 호출.

    프로덕션 설정은 web_search를 강제한다. chat.completions 경로는 기존 측정 호환과
    로컬 개발만을 위한 것으로, Settings가 production+False 조합을 부팅 단계에서 거부한다.
    """
    if settings.OPENAI_CHATGPT_USE_WEB_SEARCH:
        return await _query_chatgpt_with_search(query)
    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_QUERY,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_CHATGPT},
            {"role": "user", "content": query},
        ],
        temperature=0.7,
        max_tokens=800,
    )
    return response.choices[0].message.content or ""


async def _query_chatgpt_with_search(query: str) -> str:
    """OpenAI Responses API + web_search tool. 실제 ChatGPT Search 사용자 답변에 더 가깝다.
    SDK가 지원하지 않거나 빈 응답이면 빈 문자열 반환 (호출자가 FAILED로 처리)."""
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
        return ""
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    # 일부 SDK는 output_text가 없고 output 리스트 — 가장 첫 텍스트 블록 추출
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    return ""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _query_gemini(query: str) -> str:
    client = _get_gemini_client()
    if not client:
        return ""
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
    return response.text or ""


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
    # 빠른 사전 필터 — 병원명·응답 양쪽을 정규화(공백/특수문자 제거) 후 앞 2글자 비교.
    normalized_name = _normalize_for_prefilter(hospital_name)
    normalized_response = _normalize_for_prefilter(response_text)
    if normalized_name[:2] and normalized_name[:2] not in normalized_response:
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
        return json.loads(result.choices[0].message.content or "{}")
    except Exception:
        return {
            "is_mentioned": False,
            "mention_rank": None,
            "sentiment": None,
            "mention_context": None,
        }


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
    query_fn = _query_chatgpt if platform == "chatgpt" else _query_gemini

    async def single():
        async with _get_semaphore():
            try:
                raw = await query_fn(query_text)
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
                }
            try:
                parsed = await _parse_mention(hospital_name, raw)
                comp_mentions = (
                    await _parse_competitors(competitors or [], raw) if competitors else []
                )
                return {**parsed, "raw_response": raw, "competitor_mentions": comp_mentions or None}
            except Exception as e:
                # 쿼리는 성공했으나 파싱만 실패 — 측정을 FAILED로 만들지 말고 raw를 보존하고
                # 기본값(미언급)으로 처리한다 (raw_response 비어있지 않으면 SUCCESS로 집계됨).
                logger.warning(f"Parse failed (query ok): {e}")
                return {
                    "is_mentioned": False,
                    "mention_rank": None,
                    "sentiment": None,
                    "mention_context": None,
                    "raw_response": raw,
                    "competitor_mentions": None,
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

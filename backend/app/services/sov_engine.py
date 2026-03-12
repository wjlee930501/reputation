"""SoV 엔진 — 쿼리 자동 생성·발송·파싱·계산"""
import asyncio
import json
import logging
from itertools import product

from google import genai as google_genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

logger = logging.getLogger(__name__)
_api_semaphore = asyncio.Semaphore(5)  # 전체 외부 API 동시 호출 제한
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
_gemini_client: google_genai.Client | None = None


def _get_gemini_client() -> google_genai.Client | None:
    global _gemini_client
    if settings.GEMINI_API_KEY and _gemini_client is None:
        _gemini_client = google_genai.Client(api_key=settings.GEMINI_API_KEY)
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

[답변]
{response}

반드시 아래 JSON만 출력:
{{"is_mentioned": true/false, "mention_rank": null 또는 정수, "sentiment": "positive"/"neutral"/"negative"/null, "mention_context": "언급 문장 또는 null"}}"""


def generate_query_matrix(region: list[str], specialties: list[str], keywords: list[str]) -> list[str]:
    # 🔴 CRITICAL fix: empty inputs cause product() to yield zero combinations,
    # returning an empty list. Without this guard, V0 report runs with 0 queries
    # and produces a meaningless 0% SoV result silently.
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
        q = template.format(region=main_region, sub_region=sub_region, keyword=keyword, specialty=specialty)
        queries.add(q)
    return list(queries)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _query_chatgpt(query: str) -> str:
    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_QUERY,
        messages=[
            {"role": "system", "content": "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."},
            {"role": "user", "content": query},
        ],
        temperature=1.0,
        max_tokens=800,
    )
    return response.choices[0].message.content or ""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _query_gemini(query: str) -> str:
    client = _get_gemini_client()
    if not client:
        return ""
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.GEMINI_MODEL,
        contents=query,
        config=genai_types.GenerateContentConfig(
            temperature=1.0,
            max_output_tokens=800,
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        ),
    )
    return response.text or ""


async def _parse_mention(hospital_name: str, response_text: str) -> dict:
    if not response_text.strip():
        return {"is_mentioned": False, "mention_rank": None, "sentiment": None, "mention_context": None}
    # 빠른 사전 필터
    if hospital_name[:2] not in response_text:
        return {"is_mentioned": False, "mention_rank": None, "sentiment": None, "mention_context": None}

    result = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL_PARSE,
        messages=[{"role": "user", "content": PARSE_PROMPT.format(
            response=response_text[:3000], hospital_name=hospital_name
        )}],
        temperature=0, max_tokens=200,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(result.choices[0].message.content or "{}")
    except Exception:
        return {"is_mentioned": False, "mention_rank": None, "sentiment": None, "mention_context": None}


async def run_single_query(hospital_name: str, query_text: str, platform: str, repeat_count: int) -> list[dict]:
    query_fn = _query_chatgpt if platform == "chatgpt" else _query_gemini

    async def single():
        async with _api_semaphore:
            try:
                raw = await query_fn(query_text)
                parsed = await _parse_mention(hospital_name, raw)
                return {**parsed, "raw_response": raw}
            except Exception as e:
                logger.error(f"Query failed: {e}")
                return {"is_mentioned": False, "mention_rank": None, "sentiment": None, "mention_context": None, "raw_response": ""}

    return list(await asyncio.gather(*[single() for _ in range(repeat_count)]))


def calculate_sov(results: list[dict]) -> float:
    if not results:
        return 0.0
    return round(sum(1 for r in results if r.get("is_mentioned")) / len(results) * 100, 2)

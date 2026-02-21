"""
콘텐츠 생성 엔진 — Claude Sonnet 기반
- 병원 프로파일 기반 콘텐츠 자동 생성
- 유형별 프롬프트 분기
- 의료광고 금지 표현 자동 필터 + 재생성
"""
import logging
import re

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentType
from app.models.hospital import Hospital

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# ── 의료광고 금지 표현 ────────────────────────────────────────────
FORBIDDEN_EXPRESSIONS = [
    "1등", "최고", "최우수", "유일", "완치", "100%",
    "성공률", "부작용 없는", "검증된", "가장 잘하는",
    "국내 최초", "세계 최초", "특허", "독보적",
]

# ── 시스템 프롬프트 ───────────────────────────────────────────────
SYSTEM_PROMPT = """\
당신은 병원 의료 콘텐츠 전문 작가입니다.
아래 병원 정보를 바탕으로 AEO(Answer Engine Optimization) 최적화 콘텐츠를 작성합니다.

작성 규칙:
1. 첫 문단에서 핵심 내용을 완결 (ChatGPT·Gemini 인용 최적화)
2. 환자의 실제 언어로 작성 — 의학 용어 최소화, 쉽고 친근하게
3. 지역명·병원명·원장명을 자연스럽게 포함
4. 분량: 600~900자 (한국어 기준)
5. 마크다운 형식: H2 소제목 2~3개 활용
6. 의료광고법 준수 — 아래 표현 절대 사용 금지:
   1등, 최고, 최우수, 유일, 완치, 100%, 성공률, 부작용 없는, 가장 잘하는, 국내 최초

출력 형식 (JSON):
{
  "title": "콘텐츠 제목 (50자 이내)",
  "body": "본문 마크다운",
  "meta_description": "검색 결과·AI 답변용 요약 (150자 이내)"
}
"""

# ── 유형별 사용자 프롬프트 ────────────────────────────────────────
TYPE_PROMPTS = {
    ContentType.FAQ: """\
[콘텐츠 유형: FAQ]
환자가 ChatGPT에 실제로 물어볼 만한 질문 1개를 선정하고 답변을 작성하세요.
질문 형식: "H1 제목으로 질문 → H2 소제목으로 핵심 답변 → 상세 설명"
진료 키워드: {keywords}
""",
    ContentType.DISEASE: """\
[콘텐츠 유형: 질환 가이드]
아래 질환 중 하나를 선택하여 원인·증상·진단·치료법을 환자 관점에서 설명하세요.
진료 키워드: {keywords}
""",
    ContentType.TREATMENT: """\
[콘텐츠 유형: 시술·치료 안내]
아래 시술 중 하나의 과정·회복 기간·주의사항을 안심할 수 있는 톤으로 설명하세요.
진료 키워드: {keywords}
""",
    ContentType.COLUMN: """\
[콘텐츠 유형: 원장 칼럼]
원장님의 시각에서 환자에게 전하는 의견형 글을 작성하세요.
원장명이 자연스럽게 3회 이상 등장해야 합니다 (AI 인용 시 전문의명 co-occurrence 목적).
원장명: {director_name}
전문 분야: {specialties}
진료 철학: {director_philosophy}
""",
    ContentType.HEALTH: """\
[콘텐츠 유형: 건강 정보]
계절·생활습관 관련 예방 정보를 친근하게 작성하세요.
진료 키워드: {keywords}
""",
    ContentType.LOCAL: """\
[콘텐츠 유형: 지역 특화]
"{region} {keywords[0]}" 관련 콘텐츠로, 로컬 AI 검색에서 직접 노출되도록 지역명을 자연스럽게 반복 포함하세요.
지역: {region}
진료 키워드: {keywords}
""",
    ContentType.NOTICE: """\
[콘텐츠 유형: 병원 공지]
병원의 최근 소식·장비·서비스를 신뢰감 있게 안내하세요.
진료 내용: {treatments}
""",
}


def _build_profile_context(hospital: Hospital) -> str:
    """병원 프로파일을 프롬프트용 텍스트로 변환"""
    treatments_text = "\n".join(
        f"- {t.get('name', '')}: {t.get('description', '')}"
        for t in (hospital.treatments or [])
    )
    return f"""
[병원 프로파일]
병원명: {hospital.name}
주소: {hospital.address}
전화: {hospital.phone}
진료시간: {hospital.business_hours or ''}
지역: {', '.join(hospital.region or [])}
진료과목: {', '.join(hospital.specialties or [])}
핵심 키워드: {', '.join(hospital.keywords or [])}

원장명: {hospital.director_name or ''}
원장 약력: {hospital.director_career or ''}
진료 철학: {hospital.director_philosophy or ''}

진료 항목:
{treatments_text}
""".strip()


def _check_forbidden(text: str) -> list[str]:
    """금지 표현 포함 여부 검사. 포함된 표현 목록 반환"""
    return [expr for expr in FORBIDDEN_EXPRESSIONS if expr in text]


def _fill_type_prompt(content_type: ContentType, hospital: Hospital) -> str:
    template = TYPE_PROMPTS.get(content_type, "")
    region_text = " ".join(hospital.region or [])
    return template.format(
        keywords=", ".join(hospital.keywords or []),
        specialties=", ".join(hospital.specialties or []),
        director_name=hospital.director_name or "",
        director_philosophy=hospital.director_philosophy or "",
        region=region_text,
        treatments=[t.get("name", "") for t in (hospital.treatments or [])],
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def generate_content(
    hospital: Hospital,
    content_type: ContentType,
    existing_titles: list[str] | None = None,
) -> dict:
    """
    Claude Sonnet으로 콘텐츠 생성.
    Returns: {"title": str, "body": str, "meta_description": str}
    """
    import asyncio
    import json

    profile_ctx = _build_profile_context(hospital)
    type_prompt = _fill_type_prompt(content_type, hospital)

    avoid_titles = ""
    if existing_titles:
        avoid_titles = f"\n\n이미 작성된 제목 (중복 금지):\n" + "\n".join(f"- {t}" for t in existing_titles)

    user_message = f"{profile_ctx}\n\n{type_prompt}{avoid_titles}"

    # asyncio에서 sync anthropic 클라이언트 호출
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        ),
    )

    raw = response.content[0].text

    # JSON 파싱
    try:
        # 마크다운 코드블록 제거
        clean = re.sub(r"```(?:json)?\n?", "", raw).strip().rstrip("```").strip()
        result = json.loads(clean)
    except json.JSONDecodeError:
        logger.error(f"Content JSON parse failed: {raw[:200]}")
        raise ValueError(f"Claude returned invalid JSON: {raw[:100]}")

    # 금지 표현 검사
    full_text = result.get("title", "") + result.get("body", "")
    violations = _check_forbidden(full_text)
    if violations:
        logger.warning(f"Forbidden expressions found: {violations} — retrying")
        raise ValueError(f"Forbidden medical expressions: {violations}")

    return result

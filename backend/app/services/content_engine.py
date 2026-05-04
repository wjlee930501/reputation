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
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# ── 시스템 프롬프트 ───────────────────────────────────────────────
SYSTEM_PROMPT = """\
당신은 병원 의료 콘텐츠 전문 작가입니다.
아래 병원 정보를 바탕으로 ChatGPT·Gemini가 병원을 잘 이해할 수 있는 의료 콘텐츠를 작성합니다.

작성 규칙:
1. 첫 문단에서 환자 질문에 대한 답을 먼저 제시합니다. ChatGPT·Gemini가 그대로 이해해도 어색하지 않아야 합니다.
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
원장명이 자연스럽게 등장해야 합니다. 억지 반복 없이 전문성과 진료 철학이 연결되어야 합니다.
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
"{region} {keywords[0]}" 관련 콘텐츠로, 환자가 지역과 증상을 함께 물었을 때 AI가 병원을 이해하기 쉽도록 지역명을 자연스럽게 포함하세요.
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


def _build_philosophy_context(philosophy: HospitalContentPhilosophy | None) -> str:
    if not philosophy:
        return ""
    treatments = "\n".join(
        f"- {item.get('treatment', '진료 항목')}: {item.get('angle', '')}"
        for item in (philosophy.treatment_narratives or [])
        if isinstance(item, dict)
    )
    return f"""
[승인된 콘텐츠 철학]
version: {philosophy.version}
positioning_statement: {philosophy.positioning_statement or ''}
doctor_voice: {philosophy.doctor_voice or ''}
patient_promise: {philosophy.patient_promise or ''}
content_principles:
{_bullet_list(philosophy.content_principles or [])}
tone_guidelines:
{_bullet_list(philosophy.tone_guidelines or [])}
must_use_messages:
{_bullet_list(philosophy.must_use_messages or [])}
avoid_messages:
{_bullet_list(philosophy.avoid_messages or [])}
medical_ad_risk_rules:
{_bullet_list(philosophy.medical_ad_risk_rules or [])}
treatment_narratives:
{treatments}

규칙:
- 위 콘텐츠 철학 밖의 병원 고유 주장, 장비, 수상, 치료 효과, 비교 우위를 새로 만들지 마세요.
- 근거 자료에서 확인된 메시지와 운영자가 덧붙인 작성 방향을 섞어 과장하지 마세요.
- avoid_messages와 medical_ad_risk_rules에 해당하는 표현은 사용하지 마세요.
""".strip()


def _build_content_brief_context(content_brief: dict | None) -> str:
    if not content_brief:
        return ""

    return f"""
[승인된 콘텐츠 가이드]
target_query: {content_brief.get('target_query') or ''}
patient_intent: {content_brief.get('patient_intent') or ''}
treatment_narrative: {content_brief.get('treatment_narrative') or ''}
must_use_messages:
{_bullet_list(content_brief.get('must_use_messages') or [])}
avoid_messages:
{_bullet_list(content_brief.get('avoid_messages') or [])}
medical_risk_rules:
{_bullet_list(content_brief.get('medical_risk_rules') or [])}
internal_link_target: {content_brief.get('internal_link_target') or ''}
operator_notes:
{_bullet_list(content_brief.get('operator_notes') or [])}
""".strip()


def _bullet_list(values: list) -> str:
    return "\n".join(f"- {value}" for value in values if value) or "- 없음"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def generate_content(
    hospital: Hospital,
    content_type: ContentType,
    existing_titles: list[str] | None = None,
    philosophy: HospitalContentPhilosophy | None = None,
    content_brief: dict | None = None,
) -> dict:
    """
    Claude Sonnet으로 콘텐츠 생성.
    Returns: {"title": str, "body": str, "meta_description": str}
    """
    import asyncio
    import json

    profile_ctx = _build_profile_context(hospital)
    philosophy_ctx = _build_philosophy_context(philosophy)
    brief_ctx = _build_content_brief_context(content_brief)
    type_prompt = _fill_type_prompt(content_type, hospital)

    avoid_titles = ""
    if existing_titles:
        avoid_titles = "\n\n이미 작성된 제목 (중복 금지):\n" + "\n".join(f"- {t}" for t in existing_titles)

    essence_context = f"\n\n{philosophy_ctx}" if philosophy_ctx else ""
    brief_context = f"\n\n{brief_ctx}" if brief_ctx else ""
    user_message = f"{profile_ctx}{essence_context}{brief_context}\n\n{type_prompt}{avoid_titles}"

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
    full_text = result.get("title", "") + result.get("body", "") + result.get("meta_description", "")
    violations = check_forbidden(full_text)
    if violations:
        logger.warning(f"Forbidden expressions found: {violations} — retrying")
        raise ValueError(f"Forbidden medical expressions: {violations}")

    return result

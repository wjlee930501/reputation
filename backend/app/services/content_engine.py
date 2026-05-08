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
# GEO/AEO 신호 강화:
# - 첫 단락에 200자 이내 직접 답변 (AEO featured snippet / ChatGPT 인용 친화)
# - 본문 마지막에 참고 자료 1~2개 (GEO 도메인 권위 / 인용 신뢰 신호)
# - meta_description은 TL;DR 박스로도 노출되므로 핵심 답변 1~2문장으로 작성
SYSTEM_PROMPT = """\
당신은 병원 의료 콘텐츠 전문 작가입니다.
아래 병원 정보를 바탕으로 ChatGPT·Gemini가 병원을 잘 이해하고 답변에 인용할 수 있는 의료 콘텐츠를 작성합니다.

작성 규칙:
1. 첫 단락(약 150~200자)에 환자 질문에 대한 직접 답변을 먼저 제시합니다.
   AI가 이 단락만 발췌해도 답이 완성되도록, 핵심 결론을 첫 1~2문장에 두세요.
2. 환자의 실제 언어로 작성 — 의학 용어 최소화, 쉽고 친근하게.
3. 지역명·병원명·원장명을 자연스럽게 포함 (브랜드/엔티티 신호).
4. 분량: 본문 600~900자 (한국어 기준, 참고 자료 제외).
5. 마크다운 형식: H2 소제목 2~3개 + 필요 시 목록·짧은 인용.
6. 의료광고법 준수 — 아래 표현 절대 사용 금지:
   1등, 최고, 최우수, 유일, 완치, 100%, 성공률, 부작용 없는, 가장 잘하는, 국내 최초.
7. meta_description은 환자가 검색·AI 답변에서 처음 보는 1~2문장 요약.
   첫 단락의 직접 답변과 같은 결론을 100~150자로 압축하세요.
8. 본문 끝에 신뢰할 수 있는 참고 자료 1~2개를 references 필드로 명시합니다.
   대한의학회 / 질병관리청 / 식품의약품안전처 / 학회 가이드라인 / 대형 병원의
   공개 의학 자료 등 1차 자료를 우선합니다. 임의의 블로그/카페/광고성 페이지 금지.

출력 형식 (JSON):
{
  "title": "콘텐츠 제목 (50자 이내)",
  "body": "본문 마크다운 (참고 자료 섹션은 포함하지 않음)",
  "meta_description": "TL;DR — 1~2문장 직접 답변 (100~150자)",
  "references": [
    {"title": "출처 제목", "url": "https://..."},
    {"title": "출처 제목", "url": "https://..."}
  ],
  "faq_question": "FAQ 유형일 때만 채움 — 환자가 실제로 묻는 짧은 질문 한 문장(120자 이내). 다른 유형은 null.",
  "faq_answer_summary": "FAQ 유형일 때만 채움 — 짧고 직접적인 답변 1~2문장(180자 이내). FAQPage rich result에 들어감. 다른 유형은 null."
}
"""

# ── 유형별 사용자 프롬프트 ────────────────────────────────────────
TYPE_PROMPTS = {
    ContentType.FAQ: """\
[콘텐츠 유형: FAQ — Google FAQPage rich result 매핑]
환자가 ChatGPT에 실제로 물어볼 만한 질문 1개를 선정하고 답변을 작성하세요.
출력 필드 매핑:
- faq_question: 환자가 실제로 묻는 짧은 질문 한 문장 (120자 이내).
- faq_answer_summary: 짧고 직접적인 답변 1~2문장 (180자 이내). FAQPage Answer로 그대로 들어감.
- title: faq_question을 검색 친화 형태로 다듬은 제목 (50자 이내).
- body: 짧은 답변에 대한 상세 설명 (H2 2~3개 + 본문 600~900자).
진료 키워드: {keywords}
""",
    ContentType.DISEASE: """\
[콘텐츠 유형: 질환 가이드 — Schema.org MedicalCondition 매핑]
아래 질환 중 하나를 선택하여 다음 H2 구조로 작성하세요:
- H2 "## 증상" — 환자가 인지할 수 있는 주요 증상 3~5개.
- H2 "## 원인" — 일반적인 원인·위험 요인.
- H2 "## 진단" — 병원에서 어떤 검사·진료가 이루어지는지.
- H2 "## 치료" — 일반적 치료 방향. 효과 보장 표현 금지.
첫 단락은 환자 질문 한 줄에 대한 핵심 답변으로 시작.
진료 키워드: {keywords}
""",
    ContentType.TREATMENT: """\
[콘텐츠 유형: 시술·치료 안내 — Schema.org MedicalProcedure + HowTo 매핑]
아래 시술 중 하나의 과정을 단계형 마크다운으로 작성하세요:
- 첫 단락에 시술 개요 1~2문장 (안심 톤).
- H2 "## 진행 단계" 아래 "### 1단계 ...", "### 2단계 ...", "### 3단계 ..." 형식으로
  3~4단계를 명확히 구분 (HowTo schema 자동 추출용).
- H2 "## 회복과 주의사항" 으로 마무리.
효과 보장·통증 없음·100% 같은 단정 금지.
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
[승인된 콘텐츠 운영 기준]
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
- 위 콘텐츠 운영 기준 밖의 병원 고유 주장, 장비, 수상, 치료 효과, 비교 우위를 새로 만들지 마세요.
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

    # 참고 자료 정규화 — Claude가 형식을 살짝 흔들어도 list[{title,url}] 형태로 통일.
    result["references"] = _normalize_references(result.get("references"))

    # FAQ 분리 필드 정규화 — 다른 type일 때는 None.
    result["faq_question"] = _trim_or_none(result.get("faq_question"), 300)
    result["faq_answer_summary"] = _trim_or_none(result.get("faq_answer_summary"), 600)

    return result


def _trim_or_none(value: object, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _normalize_references(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not (title and url):
            continue
        if not (url.startswith("https://") or url.startswith("http://")):
            continue
        cleaned.append({"title": title[:200], "url": url[:500]})
    return cleaned

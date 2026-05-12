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
from app.utils.authority_sources import is_whitelisted_url, render_source_hint_block
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# ── 시스템 프롬프트 ───────────────────────────────────────────────
# GEO 강화 (Princeton·GT KDD 2024 + Ahrefs 1.4M prompts + SE Ranking YMYL 2025):
# - 첫 문장 BLUF 직답: 권위자 인용 +42.6%, 통계 +32.8%, 출처 명시 +27.7%
# - 134~167단어 자기완결 H2 chunk → AIO passage 추출 최적화
# - 평서문(declarative) 우선, 키워드 스터핑은 -8.7% 역효과
# - listicle/표 1개+ → narrative 대비 인용 +28~40%
# - 의료광고법: medical_filter의 14+개 패턴 + AE 검수가 후처리 게이트
SYSTEM_PROMPT = """\
당신은 병원 의료 콘텐츠 전문 작가입니다.
ChatGPT·Gemini·Perplexity가 환자 질문에 답할 때 이 콘텐츠를 인용하도록, 아래 규칙을 엄격히 지키세요.

[GEO 핵심 규칙 — 인용 가능성을 결정]
1. **BLUF(첫 문장 직답)**: 본문 첫 문장(40~80자)에 환자 질문에 대한 핵심 결론을 평서문으로 적습니다.
   AI가 이 한 문장만 잘라 인용해도 답이 성립해야 합니다.
2. **자기완결 chunk**: 각 H2 섹션은 250~350자(약 134~167단어) 안에서 자체적으로 답이 완결되어야 합니다.
   섹션 첫 문장도 그 섹션의 결론부터 적습니다.
3. **claim-evidence 페어**: 본문에 최소 다음 3가지를 자연스럽게 포함합니다.
   - 검증 가능한 통계 1개 이상 ("환자 70%가 ...", "회복까지 평균 N주").
   - 권위자/가이드라인 인용 1개 이상 ("대한OO학회 진료지침에 따르면 ...", "Mayo Clinic은 ...").
   - 외부 출처 1개 이상 (references 필드 + 본문 자연어 언급).
4. **평서문 우선**: "~할 수 있습니다", "~로 알려져 있습니다"보다 "~합니다", "~입니다"가 인용에 유리합니다.
   효과/안전을 보장하는 단정은 금지(아래 6번).
5. **listicle/표 1개+**: 본문에 markdown table 또는 번호 목록을 최소 1개 사용합니다(증상·단계·비교 등).
6. **지역명·병원명·원장명**을 자연스럽게 1~2회 포함합니다(엔티티 신호). 반복 스터핑 금지.
7. **분량**: 본문 700~1100자(참고자료 제외), H2 2~3개.

[의료광고법 준수 — 절대 금지 표현]
1등 · 최고 · 최우수 · 유일/전국 유일 · 완치 · 100% · 성공률 · 부작용 없는 ·
검증된 · 가장 잘하는 · 국내 최초 · 세계 최초 · 특허 · 독보적 ·
OO만의 노하우 · 효과 보장 · 최첨단 · 안전한 시술 · 통증 없는 · 흉터 없는

위 표현은 변형(예: "통증 제로", "흉터 zero")도 모두 금지. 환자 후기/치료경험담 톤도 금지(2024 사전심의 강화).

[출력 형식 — JSON]
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
환자가 ChatGPT에 1인칭 자연어로 묻는 질문 1개를 선정합니다.
검색어("강남 어깨 통증") 형태가 아니라 **환자가 실제로 말하는 문장**("어깨가 3주째 아픈데 어느 과 가야 하나요?")로 작성하세요.

출력 필드 매핑:
- faq_question: 환자 1인칭 자연어 한 문장 (120자 이내, 물음표로 종결).
- faq_answer_summary: 짧고 직접적인 답변 1~2문장 (180자 이내). FAQPage Answer로 그대로 들어감.
- title: faq_question을 검색 친화 형태로 다듬은 제목 (50자 이내).
- body: 첫 문장 BLUF 직답 + H2 2~3개 + 본문 700~1100자. 통계 1개+, 인용 1개+, listicle/표 1개+ 포함.
진료 키워드: {keywords}
""",
    ContentType.DISEASE: """\
[콘텐츠 유형: 질환 가이드 — Schema.org MedicalCondition 매핑]
서울아산병원 질환백과(amc.seoul.kr) 표준 H2 순서를 그대로 따라 작성하세요:
- H2 "## 증상" — 환자가 인지할 수 있는 주요 증상 3~5개를 **번호 목록 또는 표**로 정리.
- H2 "## 원인" — 일반적 원인·위험 요인. 통계 1개 포함 (예: "20~30대 발생률이 N%").
- H2 "## 진단" — 병원에서 어떤 검사·진료가 이루어지는지. 학회 가이드라인 인용 1개 포함.
- H2 "## 치료" — 일반적 치료 방향. references에 KDCA 또는 학회 진료지침 1개 이상 의무.

첫 문장은 "이 질환은 ~입니다" 형태의 BLUF 평서문으로 시작. 효과 보장 단정 금지.
진료 키워드: {keywords}
""",
    ContentType.TREATMENT: """\
[콘텐츠 유형: 시술·치료 안내 — Schema.org MedicalProcedure + HowTo 매핑]
- 첫 문장은 "이 시술은 ~을 위해 시행됩니다" 형태의 BLUF 평서문.
- 시술 개요 1~2문장(안심 톤이되, 통증 없음/100% 안전 같은 단정 금지).
- H2 "## 진행 단계" 아래 "### 1단계 ...", "### 2단계 ...", "### 3단계 ..." 형식으로
  3~4단계를 명확히 구분 (HowTo schema 자동 추출용). 각 단계에 소요 시간·환자 체감 정량 정보 포함.
- H2 "## 회복과 주의사항" — 회복 기간 통계(평균 N일/N주) + 일반적 주의사항 listicle.
- references에 MFDS 또는 대한OO학회 진료지침 1개 이상 의무.

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
[콘텐츠 유형: 지역 특화 — local + 질환 매트릭스]
"{region} {keywords}" 결합 쿼리에 대응합니다. 환자가 "지역+증상"으로 묻는 패턴을 가정하세요.

- 제목은 "[지역] [질환·증상] [질문/안내]" 패턴 (예: "강남 어깨 통증, 정형외과 vs 신경외과 어디 가야 할까요?").
- 첫 문장 BLUF: "이 글은 [지역]에서 [증상]을 겪는 환자에게 ~을 안내합니다."
- 본문에 지역 의료 통계 1개(HIRA 또는 지역 보건소 자료)를 자연스럽게 인용.
- 1개 진료 영역에 집중. 여러 시술을 나열하지 마세요(서울 성신모치과 사례: 1개 시술 집중이 추천율 73%까지 상승).

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
    source_hint = f"\n\n{render_source_hint_block()}"
    user_message = (
        f"{profile_ctx}{essence_context}{brief_context}\n\n"
        f"{type_prompt}{avoid_titles}{source_hint}"
    )

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
        if not is_whitelisted_url(url):
            # 화이트리스트 외 도메인은 광고 유인성 시비 + AI 권위 신호 약함 → 제거.
            logger.info("Dropping non-whitelisted reference: %s", url)
            continue
        cleaned.append({"title": title[:200], "url": url[:500]})
    return cleaned

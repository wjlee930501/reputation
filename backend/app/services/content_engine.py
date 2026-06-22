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
from app.utils.authority_sources import (
    infer_source_type,
    is_whitelisted_url,
    render_source_hint_block,
)
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

CONTENT_BODY_MIN_CHARS = 1800
CONTENT_BODY_MAX_CHARS = 5200

client = anthropic.Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=90.0,
    max_retries=0,  # tenacity handles retries with backoff
)

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
3. **claim-evidence — 단, 날조는 절대 금지(최우선 규칙)**: 인용 가능성을 높이려 통계·출처를 쓰되
   **검증할 수 없는 수치나 출처를 지어내지 마세요. 지어낸 통계/인용은 의료광고법 위반이자 허위정보입니다.**
   - 통계: 널리 정립된 일반 의학 사실만 사용(예: "대장암의 대부분은 선종성 용종에서 시작합니다").
     특정 퍼센트("재발률 40%", "환자 70%")를 **특정 기관에 귀속시키지 마세요.** 확실한 출처가 없으면
     수치를 빼고 정성적으로 적습니다("상당수", "대부분", "드뭅니다").
   - 인용: 실재하고 그 내용을 실제로 담은 공신력 기관(국가암정보센터·질병관리청·대한대장항문학회 진료지침 등)만,
     references에 실제 URL과 함께. 확신이 없으면 인용하지 않습니다.
   - **"Mayo Clinic은 40% 낮춘다" 같은 [기관명+미검증 수치] 조합은 절대 금지.** 차라리 인용을 생략하세요.
4. **평서문 우선**: "~할 수 있습니다", "~로 알려져 있습니다"보다 "~합니다", "~입니다"가 인용에 유리합니다.
   효과/안전을 보장하는 단정은 금지(아래 6번).
5. **listicle/표 1개+**: 본문에 markdown table 또는 번호 목록을 최소 1개 사용합니다(증상·단계·비교 등).
6. **지역명·병원명·원장명**을 자연스럽게 1~2회 포함합니다(엔티티 신호). 반복 스터핑 금지.
7. **분량**: 본문 1800~4200자(참고자료 제외), H2 4~6개. 5200자는 넘기지 마세요.
   각 글은 세일즈 데모에서 실제 병원 콘텐츠처럼 읽혀야 하므로 짧은 요약문처럼 끝내지 마세요.
   정의, 감별 포인트, 병원 선택 기준, 진료 흐름, 생활 관리, 내원 기준을 충분히 풀어 씁니다.

[의료광고법 준수 — 절대 금지 표현]
1등 · 최고 · 최우수 · 유일/전국 유일 · 완치 · 100% · 성공률 · 부작용 없는 ·
검증된 · 가장 잘하는 · 국내 최초 · 세계 최초 · 특허 · 독보적 ·
OO만의 노하우 · 효과 보장 · 최첨단 · 안전한 시술 · 통증 없는 · 흉터 없는

위 표현은 변형(예: "통증 제로", "흉터 zero")도 모두 금지. 환자 후기/치료경험담 톤도 금지(2024 사전심의 강화).

[최상급·단정·예후 과장 금지 — 추가]
- **최상급/단정 표현 금지**: "가장 ~"(가장 확실한·가장 좋은·가장 빠른 등), "확실한 방법", "걱정 없이",
  "~만 하면", "반드시 낫는다" 류. 효과·예방·안전을 보장처럼 보이게 하는 단정은 모두 피하세요.
- **예후·치료는 반드시 hedge**: 병기별 생존율·치료법·검사주기는 개인차가 크므로 단정하지 마세요.
  예: "1기는 내시경 절제만으로 가능"(X) → "조기 병변은 경우에 따라 내시경 절제로 치료하기도 합니다"(O).
  "정상이면 5년간 걱정 없다"(X) → "정상 소견이어도 정기적 추적이 필요합니다"(O).
  "당일 절제할 수 있습니다"(X) → "상태에 따라 같은 날 절제가 가능한 경우도 있습니다"(O).
- **생존율·통계 수치 표는 검증 가능한 출처(국가암정보센터 요약병기 상대생존율 등)가 있을 때만**, 본문에
  출처를 명시해 사용하세요. 출처가 없으면 표·수치를 빼고 정성적으로 서술합니다.

[병원 사실 — 프로파일 밖 날조 금지(허위 의료표시 방지)]
- **시술·수술법·마취법·검사·장비·진료범위는 [병원 프로파일]·[승인된 콘텐츠 가이드]에 있는 것만** 본원이
  제공하는 것처럼 쓰세요. 프로파일에 없는 시술/검사(예: 본원이 하지 않는 PPH·고무밴드결찰술·바이오피드백 등)를
  본원 서비스인 양 단정하지 마세요. 일반 의학 설명이 필요하면 "일반적으로 ~한 방법도 있습니다"처럼
  본원 제공과 명확히 구분합니다.
- **의료진 자격·경력·출신은 프로파일에 명시된 것만** 사용하세요. 없는 자격(예: 'OO 세부전문의')이나
  경력('OO 출신')을 지어내지 마세요. 자격명은 프로파일 표기 그대로 씁니다.
- 가이드의 must_use_messages(병원 핵심 시술·강점)가 있으면 본문에 자연스럽게 반영하고, avoid_messages는 피합니다.
- 회복기간·입원·마취 후 경과 등은 프로파일/가이드의 실제 운영 방침과 어긋나지 않게 적습니다.

[출력 형식 — JSON]
설명, 마크다운 코드블록, ```json fence 없이 JSON 객체만 출력하세요.
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
- body: 첫 문장 BLUF 직답 + H2 4~6개 + 본문 1800~4200자. listicle/표 1개+ 포함.
  통계·인용은 검증 가능한 공신력 출처가 있을 때만 출처와 함께(없으면 정성적으로 서술; 수치·기관명 날조 금지).
진료 키워드: {keywords}
""",
    ContentType.DISEASE: """\
[콘텐츠 유형: 질환 가이드 — Schema.org MedicalCondition 매핑]
서울아산병원 질환백과(amc.seoul.kr) 표준 H2 순서를 그대로 따라 작성하세요:
- H2 "## 증상" — 환자가 인지할 수 있는 주요 증상 3~5개를 **번호 목록 또는 표**로 정리.
- H2 "## 원인" — 일반적 원인·위험 요인. 통계는 검증 가능한 공신력 출처에 있을 때만 출처와 함께; 없으면 빈도·경향으로 정성 서술(수치·기관명 날조 금지).
- H2 "## 진단" — 병원에서 어떤 검사·진료가 이루어지는지. 인용은 실제 확인되는 가이드라인만(없으면 생략).
- H2 "## 치료" — 일반적 치료 방향. 검증 가능한 공신력 출처(KDCA·학회 진료지침)가 있으면 references에 실제 URL로 포함(없으면 생략; 가짜 출처 금지).

첫 문장은 "이 질환은 ~입니다" 형태의 BLUF 평서문으로 시작. 효과 보장 단정 금지.
진료 키워드: {keywords}
""",
    ContentType.TREATMENT: """\
[콘텐츠 유형: 시술·치료 안내 — Schema.org MedicalProcedure + HowTo 매핑]
- 첫 문장은 "이 시술은 ~을 위해 시행됩니다" 형태의 BLUF 평서문.
- 시술 개요 1~2문장(안심 톤이되, 통증 없음/100% 안전 같은 단정 금지).
- H2 "## 진행 단계" 아래 "### 1단계 ...", "### 2단계 ...", "### 3단계 ..." 형식으로
  3~4단계를 명확히 구분 (HowTo schema 자동 추출용). 소요 시간 등은 일반적 범위로 적되 개인차가 있음을 명시(확정 수치 단정 금지).
- H2 "## 회복과 주의사항" — 회복 흐름과 일반적 주의사항 listicle. 회복 기간은 개인차가 크므로 단정하지 말고 일반적 경향으로 서술.
- 검증 가능한 공신력 출처(MFDS·대한OO학회 진료지침)가 있으면 references에 실제 URL로 포함(없으면 생략; 가짜 출처 금지).

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
- 지역 의료 통계는 실제 확인되는 공신력 자료(HIRA·보건소)가 있을 때만 출처와 함께 인용; 없으면 지역 맥락을 정성적으로 서술(지역 수치 날조 금지).
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
            max_tokens=5500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        ),
    )

    raw = response.content[0].text

    result = _parse_json_response(raw, json_module=json)

    _validate_body_length(result.get("body"))

    # FAQ 분리 필드 정규화 — 다른 type일 때는 None. 금지 표현 검사 전에 정규화해
    # faq_question/faq_answer_summary도 동일한 필터 경로를 타게 한다 (P1-2).
    result["faq_question"] = _trim_or_none(result.get("faq_question"), 300)
    result["faq_answer_summary"] = _trim_or_none(result.get("faq_answer_summary"), 600)

    # 금지 표현 검사 — 1차 시도 실패 시 자동 정제 (재시도보다 안정적)
    violations = check_forbidden(forbidden_check_text(result))
    if violations:
        logger.warning(f"Forbidden expressions found: {violations} — auto-sanitizing")
        result = _sanitize_forbidden(result, violations)
        remaining = check_forbidden(forbidden_check_text(result))
        if remaining:
            raise ValueError(f"Cannot sanitize forbidden medical expressions: {remaining}")

    # 참고 자료 정규화 — Claude가 형식을 살짝 흔들어도 list[{title,url}] 형태로 통일.
    result["references"] = _normalize_references(result.get("references"))

    # meta_description 컬럼은 VARCHAR(300) — 프롬프트는 100~150자를 요구하지만 모델 출력은
    # 보장이 없고, 300자 초과 시 야간 배치의 per-item 커밋이 DataError로 실패한다.
    result["meta_description"] = _trim_or_none(result.get("meta_description"), 300)

    return result


# 의료광고 금지 표현 검사 대상 필드 — 공개 표면(FAQPage rich result 포함)에 노출되는
# 모든 텍스트 필드를 빠짐없이 포함해야 한다 (P1-2: FAQ 필드 누락 회귀 방지).
FORBIDDEN_CHECK_FIELDS = (
    "title",
    "body",
    "meta_description",
    "faq_question",
    "faq_answer_summary",
)


def forbidden_check_text(result: dict) -> str:
    """생성 결과에서 금지 표현 검사 대상 텍스트를 하나로 합친다."""
    return " ".join(
        value
        for value in (result.get(field) for field in FORBIDDEN_CHECK_FIELDS)
        if isinstance(value, str) and value
    )


def _parse_json_response(raw: str, *, json_module) -> dict:
    """Parse Claude JSON, tolerating markdown fences around the object."""
    clean = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", clean, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        clean = fenced.group(1).strip()
    else:
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            clean = clean[start:end + 1]

    try:
        result = json_module.loads(clean)
    except json_module.JSONDecodeError as exc:
        logger.error("Content JSON parse failed near %s: %s", exc.pos, raw[:500])
        raise ValueError(f"Claude returned invalid JSON: {raw[:100]}") from exc

    if not isinstance(result, dict):
        raise ValueError("Claude returned JSON that is not an object")
    return result


def _trim_or_none(value: object, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _plain_content_text(value: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[#*_\[\]\(\)`>!\-|]", "", value))


def _validate_body_length(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("Generated content body is missing")

    body_length = len(_plain_content_text(value))
    if body_length < CONTENT_BODY_MIN_CHARS:
        raise ValueError(
            f"Generated content body is too short "
            f"({body_length} < {CONTENT_BODY_MIN_CHARS})"
        )
    if body_length > CONTENT_BODY_MAX_CHARS:
        raise ValueError(
            f"Generated content body is too long "
            f"({body_length} > {CONTENT_BODY_MAX_CHARS})"
        )


def _sanitize_forbidden(result: dict, violations: list[str]) -> dict:
    """Remove forbidden medical expressions from generated text as a safety net.

    Operates on every public-surface text field (title/body/meta + FAQ fields).
    Only used as a fallback when Claude generates text containing banned terms
    despite prompt instructions.
    """
    from app.utils.medical_filter import FORBIDDEN_PATTERNS, normalize_for_check

    sanitized = dict(result)
    for field in FORBIDDEN_CHECK_FIELDS:
        text = sanitized.get(field)
        if not isinstance(text, str):
            continue
        # 탐지(check_forbidden)는 NFKC 정규화된 문자열에서 매칭하므로, 제거도 동일하게
        # 정규화 후 수행해야 전각(１００％)·zero-width 변형이 실제로 지워진다. 정규화 없이
        # raw에 sub하면 탐지는 되지만 제거가 안 돼 재검사에서 hard-fail한다(리뷰 회귀).
        text = normalize_for_check(text)
        for label in violations:
            pattern = FORBIDDEN_PATTERNS.get(label)
            if pattern:
                text = pattern.sub("", text)
        sanitized[field] = text
    return sanitized


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
        entry: dict = {"title": title[:200], "url": url[:500]}
        source_type = infer_source_type(url)
        if source_type:
            entry["source_type"] = source_type
        cleaned.append(entry)
    return cleaned

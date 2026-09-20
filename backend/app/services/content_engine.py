"""
콘텐츠 생성 엔진 — Claude Sonnet 기반
- 병원 프로파일 기반 콘텐츠 자동 생성
- 유형별 프롬프트 분기
- 의료광고 금지 표현 자동 필터 + 재생성
"""
import json
import logging
import re
import uuid
from urllib.parse import urlparse

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.models.content import ContentType
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.services import llm_structured_output, openrouter
from app.services.content_similarity import (
    REFERENCE_TOKEN_MATCH_MIN,
    normalize_topic_text,
    reference_topic_match,
)
from app.services.essence_engine import (
    MANDATORY_AVOID_MESSAGES,
    MANDATORY_MEDICAL_AD_RISK_RULES,
    effective_safety_policy,
)
from app.services.openrouter import NON_RETRYABLE_LLM_ERRORS
from app.utils.authority_sources import (
    CURATED_SOURCE_URLS,
    infer_source_type,
    institution_label_for_url,
    institution_title_tokens,
    is_citable_reference_url,
    is_whitelisted_url,
    render_source_hint_block,
    select_curated_authority_sources,
)
from app.utils.medical_filter import (
    check_forbidden_content_fields,
    forbidden_vocabulary_for_prompt,
)

logger = logging.getLogger(__name__)

CONTENT_BODY_MIN_CHARS = 1800
CONTENT_BODY_MAX_CHARS = 5200
# 작가에게 제시하는 목표 구간. 하한을 저장 게이트(1,800자)에 붙여 두면 정상 편차만으로도
# 게이트 아래로 떨어지므로 여유를 둔 구간을 요구한다. 시스템 규칙·유형 템플릿·재작성
# 지적이 **모두 이 상수를 렌더**해, 한쪽만 고쳐 서로 다른 숫자를 말하는 일을 막는다.
CONTENT_BODY_TARGET_MIN_CHARS = 2400
CONTENT_BODY_TARGET_MAX_CHARS = 4500

# ── 공개 콘텐츠 품질 검증 상수 ───────────────────────────────────────────────
# HARD-FAIL (tenacity 재시도 트리거) 기준 — 최소한으로만 유지해 정상 출력이 리젝되지 않도록.
SEO_H2_MIN = 2           # ## 헤딩이 이것보다 적으면 chunk 구조 붕괴 → hard-fail
# 의료 안내 유형은 발행 자동화 전에 특정 근거 문서가 반드시 있어야 한다. NOTICE만
# 순수 운영 공지일 수 있어 예외로 둔다.
# 참고 자료가 반드시 필요한 콘텐츠 유형. **생성 검증과 발행 게이트가 같은 값을 써야 한다** —
# 따로 두면 생성은 통과하고 발행만 막혀 슬롯이 영구히 비는 유형이 생긴다(NOTICE가 그랬다).
REFERENCES_REQUIRED_TYPES: frozenset = frozenset(
    {
        ContentType.FAQ,
        ContentType.DISEASE,
        ContentType.TREATMENT,
        ContentType.COLUMN,
        ContentType.HEALTH,
        ContentType.LOCAL,
    }
)

# 결정적 검증(분량·가격·SEO·GEO·FAQ·금지 표현) 실패에 쓰는 공급자 호출 횟수.
# 같은 프롬프트로 blind 재시도하지 않고 직전 실패 사유를 작가에게 넘겨 다시 쓰게 한다.
# 총량은 종전 tenacity 3회와 같다.
GENERATION_REMEDIATION_ROUNDS = 3
# 전송 오류 재시도(tenacity)까지 같은 예산에서 센다. 이 수를 이미 쓴 뒤에는 새 재작성
# 회차를 시작하지 않는다 — 결정적 재작성 3회와 전송 재시도 3회가 곱해져 한 아이템에
# 9회를 결제하는 일을 막는다.
GENERATION_PROVIDER_CALL_BUDGET = 5
# 한 재작성 회차에 작가에게 닿는 지적 수. 결정적 거절은 회차마다 누적되므로(최대
# GENERATION_REMEDIATION_ROUNDS개) 나머지 자리를 호출자 지적(독립 검수)이 쓴다.
GENERATION_REMEDIATION_FINDING_LIMIT = 5

# SOFT-FINDING 기준 — 위반해도 생성 결과는 살리고 AE 화면에 점수로만 표시
SEO_TITLE_MAX_CHARS = 60         # Google title truncation 기준 (권고)
SEO_META_MIN_CHARS = 70          # meta_description 권고 최솟값
SEO_META_MAX_CHARS = 160         # meta_description 권고 최댓값
SEO_STAT_PATTERN = re.compile(   # 통계/수치 proxy — 숫자+단위 패턴
    r"(\d+[\.,]?\d*)\s*(%|명|건|개|회|주|일|개월|년|배|kg|cm|mm)",
    re.UNICODE,
)
UNVERIFIED_PRICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:천|만)?\s*원"),
    re.compile(r"수\s*(?:천|만)\s*원"),
    re.compile(
        r"(?:본인\s*부담|건강보험|공단|보험\s*적용|비용).{0,40}"
        r"\d+(?:\.\d+)?\s*%"
    ),
    re.compile(
        r"\d+(?:\.\d+)?\s*%.{0,40}"
        r"(?:본인\s*부담|건강보험|공단|보험\s*적용|비용)"
    ),
)
# 구체 금액·부담률은 위 패턴으로 문맥과 무관하게 계속 막는다. "무료/무상"은
# 병원 자체 제공 주장만 hard-fail하되, 무료/무상의 대상이 공적 건강검진인
# 경우만 예외로 둔다.
_HOSPITAL_SELF_FREE_PATTERN = re.compile(
    r"(?:본원|저희\s*병원|우리\s*병원|이\s*시술|진료비)[^.?!\n]{0,30}"
    r"(?:무료|무상)"
)
_PUBLIC_SCREENING_FREE_CLAIM_PATTERN = re.compile(
    r"(?:국가건강검진|일반건강검진|공단\s*(?:건강)?검진)"
    r"(?:은|는|이|가|을|를|도)?\s*"
    r"(?:(?:본원|저희\s*병원|우리\s*병원)에서\s*)?(?:무료|무상)"
    r"|(?:무료|무상)(?:로)?\s*(?:받을\s*수\s*있는\s*)?"
    r"(?:국가건강검진|일반건강검진|공단\s*(?:건강)?검진)"
)
_HOSPITAL_CARE_FREE_CLAIM_PATTERN = re.compile(
    r"(?:진료비|진료|시술)(?:\s*비용)?(?:은|는|이|가|을|를)?"
    r"[^.?!\n]{0,20}?(?:전액\s*)?(?:무료|무상)(?:로)?(?:\s*제공)?"
    r"|(?:전액\s*)?(?:무료|무상)(?:로)?(?:\s*제공(?:하는|되는)?)?"
    r"\s*(?:진료비|진료|시술)"
)
_FREE_TERM_PATTERN = re.compile(r"무료|무상")
SEASON_MONTHS = {
    "봄": {3, 4, 5},
    "여름": {6, 7, 8},
    "가을": {9, 10, 11},
    "겨울": {12, 1, 2},
}
SEASON_MISMATCH_FINDING_PREFIX = "제목 계절-발행월 불일치"


class MissingCitableReferencesError(ValueError):
    """Keep the last safe provider result available after reference retries exhaust."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


class TruncatedProviderOutputError(ValueError):
    """The provider stopped before completing the JSON envelope (max_tokens/refusal).

    This is not a JSON syntax problem and not a content gate: the same prompt will
    truncate again.  The message stays distinctive ("truncated") so the run control
    layer can classify it and the writer receives a "shorten the body" instruction
    instead of a blind identical retry.
    """


class DirectorNameMissingError(ValueError):
    """Keep the last provider result available for one deterministic name heal."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


client = openrouter.sync_client(timeout=90.0)  # max_retries=0 — tenacity가 재시도를 소유

# ── 시스템 프롬프트 ───────────────────────────────────────────────
# 검색·AI용 별도 문법을 가장하지 않고, 환자에게 유용한 고유 정보·정확한 출처·명확한
# 구조를 우선한다. 의료광고법 자동 점검과 발행 후 사람 검수는 별도 게이트에서 수행한다.
_SYSTEM_PROMPT_TEMPLATE = """\
당신은 병원 의료 콘텐츠 전문 작가입니다.
승인된 병원 자료와 공신력 있는 의료 근거를 바탕으로, 환자에게 실제로 도움이 되는 고유한 콘텐츠를 작성하세요.
검색엔진과 AI가 내용을 오해하지 않도록 질문·답·근거·병원 사실을 명확히 구분합니다.

[작성 원칙]
1. **질문에 먼저 답하기**: 첫 문단에서 환자가 가장 궁금해하는 결론과 진료가 필요한 기준을 먼저 설명합니다.
2. **병원 고유 정보 우선**: [승인된 콘텐츠 가이드]의 진료 철학·환자 약속·실제 진료 항목을 반영합니다.
   가이드에 없는 장비, 술기, 경력, 성과를 추정하거나 일반론으로 병원만의 강점처럼 포장하지 않습니다.
3. **주장과 근거**: 통계·출처를 쓰는 경우
   **검증할 수 없는 수치나 출처를 지어내지 마세요. 지어낸 통계/인용은 의료광고법 위반이자 허위정보입니다.**
   - 통계: 널리 정립된 일반 의학 사실만 사용(예: "대장암의 대부분은 선종성 용종에서 시작합니다").
     특정 퍼센트("재발률 40%", "환자 70%")를 **특정 기관에 귀속시키지 마세요.** 확실한 출처가 없으면
     수치를 빼고 정성적으로 적습니다("상당수", "대부분", "드뭅니다").
   - 인용: 실재하고 그 내용을 실제로 담은 공신력 기관(국가암정보센터·질병관리청·대한대장항문학회 진료지침 등)만,
     references에 실제 URL과 함께. 어떤 URL에 확신이 없으면 **그 URL 대신 확신이 있는 다른 공신력 문서**를 넣으세요.
     '확신이 없다'는 references를 비우라는 뜻이 아닙니다 — 참고자료가 필수인 유형에서 빈 references는 저장되지 않습니다.
   - **"Mayo Clinic은 40% 낮춘다" 같은 [기관명+미검증 수치] 조합은 절대 금지.** 그 수치를 본문에서 빼고,
     references에는 이 글의 주제를 다루는 확실한 문서를 넣으세요.
4. **명확한 문장**: 모호한 홍보 문구를 피하고, 의학적 불확실성·개인차는 정확히 표시합니다.
5. **읽기 쉬운 구조**: 페이지 제목은 시스템이 별도 H1으로 표시하므로 본문에는 `# H1`을 절대 넣지 말고,
   `## H2` 소제목부터 사용합니다. 단계·비교가 실제 이해에 도움이 될 때만 목록이나 표를 씁니다.
6. **엔티티 정확성**: 프로파일의 지역명·병원명·원장명을 표기 그대로 본문에 각각 최소 1회 자연스럽게
   포함하세요. 누락은 허용되지 않으며 반복 삽입은 금지합니다.
7. **분량**: 글자 수는 **공백과 마크다운 기호(#, *, -, |, 링크 괄호 등)를 모두 제거한 순수 글자 수**로 셉니다.
   같은 글이라도 공백까지 센 길이보다 20~35% 짧게 계산되므로, 화면에 보이는 길이가 아니라 이 기준으로 맞추세요.
   __BODY_LENGTH_TARGETS__
   이미 다른 글에 있는 일반론을 반복하지 말고, 이 질문에 필요한 감별 포인트·진료 흐름·내원 기준을 충분히 풉니다.
8. **비용 정보**: 승인된 병원 자료에 명시되지 않은 구체적 금액, '무료', 건강보험 본인부담률을 추정하지 마세요.
   비용 질문에는 진료 목적·검사 범위·보험 적용 여부에 따라 달라질 수 있으므로 의료기관에 현재 기준을 확인하라고 설명하세요.
9. **발행 시점 일치**: 콘텐츠 가이드에 planned_publish_date가 있으면 그 날짜의 계절과 맞지 않는
   봄철·여름철·가을철·겨울철 제목이나 도입을 만들지 마세요. 계절성이 필요 없으면 연중형 제목을 사용하세요.

[의료광고법 준수 — 절대 금지 표현]
__FORBIDDEN_EXPRESSIONS__

위 표현은 변형(예: "통증 제로", "흉터 zero")도 모두 금지. 환자 후기/치료경험담 톤도 금지(2024 사전심의 강화).
**부정문·인용문·통계 인용 문맥에서도 위 어휘 자체를 쓰지 말고 다른 표현으로 바꿔 쓰세요.**
(예: "완치율이 높지는 않습니다" → "치료로 증상이 좋아지는 정도는 개인차가 큽니다",
 "사망원인 1위" → "가장 흔한 사망 원인 가운데 하나", "성공률" → "치료 결과")

[플랫폼 공통 의료광고 안전 규칙 — 모든 병원에 항상 적용]
__MANDATORY_SAFETY_RULES__

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

# [작성 원칙] 7의 분량 문장. 게이트 상수에서 렌더해 프롬프트와 검증기가 다른 숫자를
# 말할 수 없게 한다.
_BODY_LENGTH_TARGETS = (
    f"목표는 순수 글자 수 {CONTENT_BODY_TARGET_MIN_CHARS:,}~"
    f"{CONTENT_BODY_TARGET_MAX_CHARS:,}자이고 H2는 4~6개입니다.\n"
    f"   순수 글자 수 {CONTENT_BODY_MIN_CHARS:,}자 미만이거나 "
    f"{CONTENT_BODY_MAX_CHARS:,}자를 넘으면 저장되지 않습니다."
)

# 작가 응답의 전송 수단. 프롬프트가 요구하는 필드와 **정확히 같은 집합**이며,
# 강제 도구 호출로 받으면 ```json fence·본문 안의 이스케이프되지 않은 큰따옴표가
# 파싱을 깨뜨릴 수 없다. 프롬프트의 [출력 형식 — JSON] 절은 그대로 두어 모델이
# 필드 의미를 같은 문장으로 읽게 한다.
ARTICLE_TOOL_NAME = "emit_article"
# FAQPage rich result 로 그대로 나가는 두 필드의 계약. 스키마 설명과 프롬프트가 같은
# 문장을 말해야 작가가 한쪽만 보고 필드를 비우지 않는다.
_FAQ_QUESTION_CONTRACT = "환자 1인칭 자연어 질문 한 문장(120자 이내, 물음표로 종결)"
_FAQ_ANSWER_CONTRACT = (
    "그 질문에 대한 직접 답변 1~2문장(180자 이내). FAQPage Answer 로 그대로 들어간다"
)
_FAQ_QUESTION_FIELD_DESCRIPTION = f"FAQ 유형에서만 채운다 — {_FAQ_QUESTION_CONTRACT}. 다른 유형은 null."
_FAQ_ANSWER_FIELD_DESCRIPTION = f"FAQ 유형에서만 채운다 — {_FAQ_ANSWER_CONTRACT}. 다른 유형은 null."
_FAQ_REQUIRED_QUESTION_DESCRIPTION = (
    f"FAQ 유형의 필수 값 — {_FAQ_QUESTION_CONTRACT}. 비우거나 생략할 수 없다."
)
_FAQ_REQUIRED_ANSWER_DESCRIPTION = (
    f"FAQ 유형의 필수 값 — {_FAQ_ANSWER_CONTRACT}. 비우거나 생략할 수 없다."
)
ARTICLE_TOOL = {
    "name": ARTICLE_TOOL_NAME,
    "description": "작성한 콘텐츠 한 편을 구조화된 필드로 제출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {
                "type": "string",
                "description": (
                    "본문 마크다운(참고 자료 섹션 제외). 공백·마크다운 기호를 제외한 순수 "
                    f"글자 수 {CONTENT_BODY_TARGET_MIN_CHARS:,}~"
                    f"{CONTENT_BODY_TARGET_MAX_CHARS:,}자. 순수 글자 수 "
                    f"{CONTENT_BODY_MIN_CHARS:,}자 미만이거나 "
                    f"{CONTENT_BODY_MAX_CHARS:,}자를 넘으면 저장되지 않는다."
                ),
            },
            "meta_description": {"type": "string"},
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["title", "url"],
                },
            },
            "faq_question": {
                "type": ["string", "null"],
                "description": _FAQ_QUESTION_FIELD_DESCRIPTION,
            },
            "faq_answer_summary": {
                "type": ["string", "null"],
                "description": _FAQ_ANSWER_FIELD_DESCRIPTION,
            },
        },
        "required": ["title", "body", "meta_description", "references"],
    },
}

# FAQ 유형 전용 스키마. 공통 스키마는 두 FAQ 필드를 nullable·optional 로 선언하므로,
# 강제 도구 호출을 쓰는 지금도 공급자 쪽에서 **빈 FAQ 출력이 유효**하다. 그 결과가
# 곧 `FAQ output requires faq_question and faq_answer_summary` 거절이고, 우리는 이미
# 결제한 글 한 편을 버린 뒤 같은 계약을 산문으로만 다시 말해 재작성을 산다. FAQ에서는
# 도구 스키마 자체가 두 필드를 요구하게 해 그 왕복을 없앤다.
#
# 스키마가 유형별로 갈리면 프롬프트 캐시 접두어도 FAQ/비FAQ 두 갈래가 되지만, 각 갈래는
# 병원·아이템과 무관하게 고정이고 한 아이템의 재작성 회차는 같은 유형이라 같은 갈래를
# 재사용한다.
_FAQ_ARTICLE_INPUT_SCHEMA = {
    **ARTICLE_TOOL["input_schema"],
    "properties": {
        **ARTICLE_TOOL["input_schema"]["properties"],
        "faq_question": {
            "type": "string",
            "description": _FAQ_REQUIRED_QUESTION_DESCRIPTION,
        },
        "faq_answer_summary": {
            "type": "string",
            "description": _FAQ_REQUIRED_ANSWER_DESCRIPTION,
        },
    },
    "required": [
        *ARTICLE_TOOL["input_schema"]["required"],
        "faq_question",
        "faq_answer_summary",
    ],
}


def _with_non_empty_references(schema: dict) -> dict:
    """빈 references 가 공급자 쪽에서 유효하지 않게 한다.

    `references`는 이미 required 지만 JSON Schema 에서 빈 배열은 유효하다. 그래서
    참고자료가 필수인 유형에서도 `references: []` 가 정상 도구 호출로 돌아오고, 우리는
    이미 결제한 글 한 편을 GEO 하드 거절로 버린 뒤 같은 계약을 산문으로만 다시 말해
    재작성을 산다. 검증기가 요구하는 것을 스키마도 요구하게 해 그 왕복을 없앤다.
    """

    references = schema["properties"]["references"]
    return {
        **schema,
        "properties": {
            **schema["properties"],
            "references": {
                **references,
                "minItems": 1,
                "description": (
                    "이 글의 주제를 다루는 화이트리스트 도메인의 실제 문서. 최소 1개가 "
                    "필요하며 빈 배열은 저장되지 않는다."
                ),
            },
        },
    }


_REFERENCE_REQUIRED_ARTICLE_INPUT_SCHEMA = _with_non_empty_references(
    ARTICLE_TOOL["input_schema"]
)
_FAQ_ARTICLE_INPUT_SCHEMA = _with_non_empty_references(_FAQ_ARTICLE_INPUT_SCHEMA)


def _article_tool_schema(content_type: ContentType) -> dict:
    """유형이 실제로 요구하는 것만 요구하는 작가 도구 스키마."""

    if content_type is ContentType.FAQ:
        return _FAQ_ARTICLE_INPUT_SCHEMA
    if content_type in REFERENCES_REQUIRED_TYPES:
        return _REFERENCE_REQUIRED_ARTICLE_INPUT_SCHEMA
    return ARTICLE_TOOL["input_schema"]


# 금지 표현 목록과 플랫폼 공통 안전 규칙은 **여기 한 번만** 렌더링한다.
# 이전에는 같은 목록이 (1) 시스템 프롬프트 하드코딩, (2) 철학 컨텍스트의
# avoid_messages, (3) 브리프 컨텍스트의 avoid_messages 로 3중 삽입돼
# 호출마다 같은 문장을 반복해서 지불했다. 유일한 원본은
# utils/medical_filter.FORBIDDEN_EXPRESSIONS 와
# essence_engine.MANDATORY_* 이며, 철학/브리프 렌더러는
# _MANDATORY_SAFETY_ENTRIES 에 있는 항목을 그대로 반복하지 않는다.
# (검사 게이트인 check_forbidden_content_fields 는 이 변경과 무관하게 유지된다.)
_MANDATORY_SAFETY_ENTRIES: frozenset[str] = frozenset(
    [*MANDATORY_AVOID_MESSAGES, *MANDATORY_MEDICAL_AD_RISK_RULES]
)

# MANDATORY_AVOID_MESSAGES 는 문장 안에 FORBIDDEN_EXPRESSIONS 를 그대로 이어 붙인
# 형태다. 위 "[의료광고법 준수 — 절대 금지 표현]" 절이 곧 그 규칙의 렌더링이므로
# 여기서 다시 찍으면 같은 목록이 한 프롬프트에 두 번 들어간다. 따라서 아래 절에는
# 표현 목록이 없는 MANDATORY_MEDICAL_AD_RISK_RULES 만 넣는다.
# 표시용 21개가 아니라 **실제 매칭 패턴이 잡는 어휘 전부**를 렌더한다. 작가가 못 보는
# 어휘로 글이 폐기되던 원인(2026-09-12 수율 계획 §1.1)을 없앤다. 값은 상수 조합이라
# 바이트 단위로 고정이므로 정적 시스템 블록(프롬프트 캐시 접두어)에 안전하다.
SYSTEM_PROMPT = _SYSTEM_PROMPT_TEMPLATE.replace(
    "__FORBIDDEN_EXPRESSIONS__", " · ".join(forbidden_vocabulary_for_prompt())
).replace(
    "__MANDATORY_SAFETY_RULES__",
    "\n".join(f"- {rule}" for rule in MANDATORY_MEDICAL_AD_RISK_RULES),
).replace("__BODY_LENGTH_TARGETS__", _BODY_LENGTH_TARGETS)

# 정적 시스템 블록 = SYSTEM_PROMPT + 권위 출처 화이트리스트. 병원·아이템과 무관하게
# 바이트 단위로 고정이라 prompt cache 접두어로 쓸 수 있다. Sonnet 계열의 최소 캐시
# 접두어는 1024 토큰이고 이 블록은 한국어 4,000자 이상(≈2.5k~4k tok)이라 여유 있게
# 넘는다. 여기에 시각·UUID 같은 변동 값을 절대 넣지 말 것 — 넣는 순간 캐시가 죽는다.
STATIC_SYSTEM_BLOCK = f"{SYSTEM_PROMPT}\n\n{render_source_hint_block()}"

# 프롬프트에 넣는 기존 제목 상한. 상한이 없으면 1년 운영한 병원에서 200편 이상의
# 제목이 매 호출에 실려 입력이 1.6~2배로 불어난다. 중복 회피에 필요한 것은
# "최근에 무엇을 썼는가"이므로 최신 N개면 충분하다.
EXISTING_TITLE_PROMPT_LIMIT = 60

# ── 유형별 사용자 프롬프트 ────────────────────────────────────────
# 유형 템플릿은 아이템 단위 사용자 메시지라 정적 시스템 블록의 [작성 원칙] 7보다 뒤에,
# 더 구체적인 지시로 읽힌다. 그래서 여기서 단위 없이 "본문 2200~4200자"라고 말하면
# 작가는 화면에 보이는 길이로 세고, 검증기가 재는 평문 기준으로는 1,400~1,760자가 되어
# 저장 하한(1,800자) 아래로 떨어진다. 유형 템플릿이 분량을 말할 때는 검증기와 같은
# 단위·같은 상수를 쓴다.
TYPE_PROMPT_BODY_LENGTH_RULE = (
    f"본문 분량은 공백·마크다운 기호를 제외한 **순수 글자 수** "
    f"{CONTENT_BODY_TARGET_MIN_CHARS:,}~{CONTENT_BODY_TARGET_MAX_CHARS:,}자입니다"
    f"(화면에 보이는 길이는 이보다 20~35% 깁니다). 순수 글자 수 "
    f"{CONTENT_BODY_MIN_CHARS:,}자 미만은 저장되지 않으므로 각 H2 절을 고르게 채우세요."
)
# 같은 이유로 참고자료도 유형 템플릿이 검증기와 같은 말을 해야 한다. FAQ·질환·시술·지역
# 템플릿은 인용을 "공신력 출처가 있을 때만 … 없으면 생략"이라고 말해 왔다. 그 문장은
# 시스템 규칙("최소 1개 반드시")보다 뒤에, 더 구체적인 지시로 읽혀 빈 references를
# 허락했고, 그것이 곧 GEO 게이트의 하드 거절이었다.
TYPE_PROMPT_REFERENCE_RULE = (
    "화이트리스트 도메인에서 확인할 수 있는 **실제 문서 URL을 references에 최소 1개** "
    "넣으세요 — 빈 references는 저장되지 않습니다. 본문에 인용할 수치가 없어도 이 글의 "
    "주제를 다루는 공신력 문서(질병관리청 국가건강정보포털·학회 진료지침·국가암정보센터 "
    "등)를 근거로 답니다. URL을 지어내지 말고, 확신이 없으면 확신이 있는 다른 문서를 "
    "쓰세요. 이 글의 주제와 다른 질환·시술을 다루는 문서는 넣지 마세요."
)

_TYPE_PROMPT_TEMPLATES = {
    ContentType.FAQ: """\
[콘텐츠 유형: FAQ — Google FAQPage rich result 매핑]
환자가 ChatGPT에 1인칭 자연어로 묻는 질문 1개를 선정합니다.
검색어("강남 어깨 통증") 형태가 아니라 **환자가 실제로 말하는 문장**("어깨가 3주째 아픈데 어느 과 가야 하나요?")로 작성하세요.

출력 필드 매핑:
- faq_question: 환자 1인칭 자연어 한 문장 (120자 이내, 물음표로 종결).
  **FAQ 유형의 필수 출력입니다. 비우거나 null로 두면 글 전체가 저장되지 않습니다.**
- faq_answer_summary: 짧고 직접적인 답변 1~2문장 (180자 이내). FAQPage Answer로 그대로 들어감.
  **이 필드도 FAQ 유형의 필수 출력입니다. 본문 첫 문단을 요약해 반드시 채우세요.**
- title: faq_question을 검색 친화 형태로 다듬은 제목 (50자 이내).
- body: 첫 문장 BLUF 직답 + H2 4~6개. listicle/표 1개+ 포함.
  __BODY_LENGTH_RULE__
  질문에 직답한 뒤 판단 기준·감별 포인트·내원 시점·진료 흐름을 각 H2에서 실제로 풀어 쓰고,
  한두 문장으로 요약만 하고 넘어가지 마세요.
  본문의 통계·수치는 검증 가능한 공신력 출처가 있을 때만 출처와 함께 쓰고, 없으면 정성적으로 서술하세요(수치·기관명 날조 금지).
__REFERENCES_RULE__
진료 키워드: {keywords}
""",
    ContentType.DISEASE: """\
[콘텐츠 유형: 질환 가이드 — Schema.org MedicalCondition 매핑]
서울아산병원 질환백과(amc.seoul.kr) 표준 H2 순서를 그대로 따라 작성하세요:
- H2 "## 증상" — 환자가 인지할 수 있는 주요 증상 3~5개를 **번호 목록 또는 표**로 정리.
- H2 "## 원인" — 일반적 원인·위험 요인. 통계는 검증 가능한 공신력 출처에 있을 때만 출처와 함께; 없으면 빈도·경향으로 정성 서술(수치·기관명 날조 금지).
- H2 "## 진단" — 병원에서 어떤 검사·진료가 이루어지는지. 본문 인용은 실제 확인되는 가이드라인만 씁니다.
- H2 "## 치료" — 일반적 치료 방향. 근거로 삼은 공신력 출처(KDCA·학회 진료지침)는 references에 실제 URL로 포함(가짜 출처 금지).

__REFERENCES_RULE__

__BODY_LENGTH_RULE__
네 절은 목차가 아니라 본문입니다. 한 절을 두세 문장으로 끝내면 전체가 저장 하한 아래로
떨어지므로, 각 절을 순수 글자 수 __DISEASE_SECTION_MIN_CHARS__자 이상으로 씁니다
(증상은 환자가 느끼는 양상과 경과, 원인은 위험 요인과 악화 조건, 진단은 검사 순서와 판단 기준,
치료는 선택지별 적응증과 회복 흐름).

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
- 근거로 삼은 공신력 출처(MFDS·대한OO학회 진료지침)는 references에 실제 URL로 포함(가짜 출처 금지).

__REFERENCES_RULE__

진료 키워드: {keywords}
""",
    ContentType.COLUMN: """\
[콘텐츠 유형: 원장 칼럼]
원장님의 시각에서 환자에게 전하는 의견형 글을 작성하세요.
원장명이 자연스럽게 등장해야 합니다. 억지 반복 없이 전문성과 진료 철학이 연결되어야 합니다.
의견형 글이어도 근거는 필요합니다. 확인할 수 없는 수치·주장은 본문에서 빼세요.
__REFERENCES_RULE__
원장명: {director_name}
전문 분야: {specialties}
진료 철학: {director_philosophy}
""",
    ContentType.HEALTH: """\
[콘텐츠 유형: 건강 정보]
계절·생활습관 관련 예방 정보를 친근하게 작성하세요.
예방·생활습관 권고에는 근거가 필요합니다.
__REFERENCES_RULE__
진료 키워드: {keywords}
""",
    ContentType.LOCAL: """\
[콘텐츠 유형: 지역 특화 — local + 질환 매트릭스]
"{region} {keywords}" 결합 쿼리에 대응합니다. 환자가 "지역+증상"으로 묻는 패턴을 가정하세요.

- 제목은 "[지역] [질환·증상] [질문/안내]" 패턴 (예: "강남 어깨 통증, 정형외과 vs 신경외과 어디 가야 할까요?").
- 첫 문장 BLUF: "이 글은 [지역]에서 [증상]을 겪는 환자에게 ~을 안내합니다."
- 지역 의료 통계는 실제 확인되는 공신력 자료(HIRA·보건소)가 있을 때만 출처와 함께 인용; 없으면 지역 맥락을 정성적으로 서술(지역 수치 날조 금지).
- 1개 진료 영역에 집중하고 여러 시술을 나열하지 마세요(집중도 높은 글이 인용에 유리).

__REFERENCES_RULE__

지역: {region}
진료 키워드: {keywords}
""",
    ContentType.NOTICE: """\
[콘텐츠 유형: 병원 공지]
승인된 병원 프로파일과 운영 기준에 명시된 사실만 안내하세요.
확인된 날짜·진료시간 변경·휴진·행사 정보가 없으면 최근 소식이나 이벤트를 만들지 말고,
등록된 진료 항목을 설명하는 상시 이용 안내로 작성하세요. 프로파일에 없는 새 장비 도입,
신규 서비스, 의료진 참여, 할인·혜택, 성과를 추정하거나 만들어내지 마세요.
진료 내용: {treatments}
""",
}

TYPE_PROMPTS = {
    content_type: template.replace(
        "__BODY_LENGTH_RULE__", TYPE_PROMPT_BODY_LENGTH_RULE
    ).replace(
        "__DISEASE_SECTION_MIN_CHARS__", f"{CONTENT_BODY_TARGET_MIN_CHARS // 4:,}"
    ).replace(
        # NOTICE는 검증기도 참고자료를 요구하지 않는다 — 요구하지 않는 유형에 규칙을
        # 렌더하면 순수 운영 공지에 없는 근거를 만들게 한다.
        "__REFERENCES_RULE__",
        TYPE_PROMPT_REFERENCE_RULE if content_type in REFERENCES_REQUIRED_TYPES else "",
    )
    for content_type, template in _TYPE_PROMPT_TEMPLATES.items()
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


# 측정 질의로 프롬프트를 조향하는 유형. COLUMN·HEALTH·NOTICE는 측정 질의가 아니라
# 원장 목소리·계절·병원 운영이 주제라 조향 대상이 아니다.
TARGET_STEERED_TYPES: frozenset = frozenset(
    {
        ContentType.FAQ,
        ContentType.DISEASE,
        ContentType.TREATMENT,
        ContentType.LOCAL,
    }
)


def _target_steering(content_brief: dict | None) -> dict[str, object]:
    """브리프에서 프롬프트 조향에 쓸 값만 뽑는다(build_content_brief가 채운 필드)."""
    brief = content_brief or {}
    return {
        "query": str(brief.get("target_query") or "").strip(),
        "question": str(brief.get("target_question") or "").strip(),
        "keyword": str(brief.get("target_keyword") or "").strip(),
        "regions": [
            str(term).strip()
            for term in (brief.get("target_region_terms") or [])
            if str(term).strip()
        ],
    }


def _target_prompt_block(
    content_type: ContentType,
    target: dict[str, object],
    profile_regions: list[str],
) -> str:
    """"이 글은 이 질문에 답한다"를 아이템 단위 사용자 메시지에 못 박는다.

    정적 시스템 블록(프롬프트 캐시 접두어)은 건드리지 않는다 — 여기 들어가는 값은
    아이템마다 바뀌므로 시스템 블록에 넣으면 캐시가 매번 깨진다.
    """
    if content_type not in TARGET_STEERED_TYPES:
        return ""
    if not (target["question"] or target["keyword"]):
        return ""

    lines = ["", "[측정된 환자 질문 — 이 글이 답해야 하는 대상]"]
    if target["query"]:
        lines.append(f"- 측정 질의 원문: {target['query']}")
    if target["question"]:
        lines.append(f"- 환자 질문 문장: {target['question']}")
    if target["keyword"]:
        lines.append(
            f"- 핵심 키워드(제목 또는 첫 H2에 반드시 그대로 포함): {target['keyword']}"
        )
    if target["regions"]:
        # 조향은 측정 지역이 앞서지만 프로파일 region을 빼고 말하지 않는다. `_validate_geo`는
        # 프로파일 region의 변형이 본문에 있어야 통과시키는 하드 게이트이고, 이 블록은
        # FAQ·DISEASE·TREATMENT·LOCAL 모두가 읽는 유일한 아이템 단위 지역 문장이다. 측정
        # 지역만 말하면 작가가 만족시킬 수 없는 요구가 되어, 지적을 되먹인 재작성마다 같은
        # `GEO hard-fail: 지역 엔티티 … 미포함`이 되풀이되고 슬롯이 GENERATION_REJECTED에
        # 갇힌다. #126은 같은 충돌을 LOCAL 유형 템플릿의 `{region}`에서만 고쳤다.
        regions = list(
            dict.fromkeys([*(str(region) for region in target["regions"]), *profile_regions])
        )
        lines.append(f"- 지역: {', '.join(regions)}")
    lines.append("- 첫 문단에서 위 환자 질문에 직접 답하세요. 질문과 무관한 주제로 넓히지 마세요.")
    if content_type is ContentType.FAQ:
        lines.append(
            "- faq_question 필드에는 위 '환자 질문 문장'을 그대로 쓰세요(다듬기는 최소한으로)."
        )
        lines.append("- faq_answer_summary는 그 질문에 대한 직접 답변이어야 합니다.")
    return "\n".join(lines) + "\n"


def _fill_type_prompt(
    content_type: ContentType,
    hospital: Hospital,
    content_brief: dict | None = None,
) -> str:
    template = TYPE_PROMPTS.get(content_type, "")
    target = _target_steering(content_brief)

    # 측정된 타깃이 있으면 병원 키워드 **전체**가 아니라 그 질문의 키워드만 넣는다.
    # 전체를 넣으면 글이 어느 질문에도 정확히 대응하지 않는 종합 안내가 된다.
    keywords_text = ", ".join(hospital.keywords or [])
    if target["keyword"] and content_type in TARGET_STEERED_TYPES:
        keywords_text = str(target["keyword"])
    # LOCAL은 **측정된 지역**을 앞세워 조향한다. 다만 프로파일 region을 지우지는 않는다 —
    # `_validate_geo`는 프로파일 region이 본문에 나타나야 통과시키는 하드 게이트라, 측정
    # 지역과 프로파일 지역이 다른 슬롯에서 프롬프트가 프로파일 지역을 보여 주지 않으면
    # 작가가 만족시킬 수 없는 요구가 된다. 그러면 지적을 되먹여도 같은 ValueError가
    # 재작성마다 되풀이돼 슬롯이 GENERATION_REJECTED에서 벗어나지 못한다.
    profile_regions = [
        str(region).strip() for region in (hospital.region or []) if str(region).strip()
    ]
    region_text = " ".join(profile_regions)
    if content_type is ContentType.LOCAL and target["regions"]:
        region_text = " ".join(
            dict.fromkeys([*(str(region) for region in target["regions"]), *profile_regions])
        )

    filled = template.format(
        keywords=keywords_text,
        specialties=", ".join(hospital.specialties or []),
        director_name=hospital.director_name or "",
        director_philosophy=hospital.director_philosophy or "",
        region=region_text,
        treatments=[t.get("name", "") for t in (hospital.treatments or [])],
    )
    return f"{filled}{_target_prompt_block(content_type, target, profile_regions)}"


def _hospital_specific_safety(philosophy: HospitalContentPhilosophy | None) -> dict[str, list[str]]:
    """플랫폼 공통 규칙을 제거한, 이 병원에만 해당하는 안전 문구.

    공통 규칙은 STATIC_SYSTEM_BLOCK 에 이미 한 번 들어가 있다. 여기서 다시 실으면
    같은 문장을 호출마다 두 번 더 지불하게 된다. 병원 고유 문구는 그대로 남긴다.
    """
    safety_policy = effective_safety_policy(philosophy)
    return {
        field: [
            value
            for value in safety_policy[field]
            if value not in _MANDATORY_SAFETY_ENTRIES
        ]
        for field in ("avoid_messages", "medical_ad_risk_rules")
    }


def _build_philosophy_context(philosophy: HospitalContentPhilosophy | None) -> str:
    if not philosophy:
        return ""
    safety_policy = _hospital_specific_safety(philosophy)
    treatments = "\n".join(
        f"- {_format_treatment_narrative(item)}"
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
{_bullet_list(safety_policy['avoid_messages'])}
medical_ad_risk_rules:
{_bullet_list(safety_policy['medical_ad_risk_rules'])}
prefer_topics (optional, later entries have higher priority):
{_bullet_list(getattr(philosophy, 'prefer_topics', []) or [])}
prefer_messages (optional, later entries have higher priority):
{_bullet_list(getattr(philosophy, 'prefer_messages', []) or [])}
- Preferences are soft guidance only, never required claims. Base must_use_messages,
  avoid_messages, medical rules and verified evidence take precedence.
treatment_narratives:
{treatments}

규칙:
- 위 콘텐츠 운영 기준 밖의 병원 고유 주장, 장비, 수상, 치료 효과, 비교 우위를 새로 만들지 마세요.
- 근거 자료에서 확인된 메시지와 운영자가 덧붙인 작성 방향을 섞어 과장하지 마세요.
- avoid_messages와 medical_ad_risk_rules에 해당하는 표현은 사용하지 마세요.
""".strip()


def _format_treatment_narrative(value: object) -> str:
    """treatment_narrative를 자연어 문장으로 조립.

    build_content_brief()가 만드는 treatment_narrative는
    {"source", "treatment", "angle", "details"} 형태의 dict다. f-string에 dict를
    그대로 넣으면 "{'source': 'approved_philosophy', ...}" 같은 Python dict 문자열이
    그대로 Claude 프롬프트에 노출돼 지시문으로 읽히지 않는다.
    """
    if isinstance(value, dict):
        treatment = str(value.get("treatment") or "").strip()
        angle = str(value.get("angle") or "").strip()
        patient_language = [
            str(item).strip()
            for item in (value.get("patient_language") or [])
            if str(item).strip()
        ]
        cautions = [
            str(item).strip() for item in (value.get("cautions") or []) if str(item).strip()
        ]
        evidence_note_ids = [
            str(item).strip()
            for item in (value.get("evidence_note_ids") or [])
            if str(item).strip()
        ]
        lines = []
        if treatment or angle:
            lines.append(f"{treatment} — {angle}" if treatment and angle else treatment or angle)
        if patient_language:
            lines.append(f"환자 설명: {' / '.join(patient_language)}")
        if cautions:
            lines.append(f"주의사항: {' / '.join(cautions)}")
        if evidence_note_ids:
            lines.append(f"근거 ID: {', '.join(evidence_note_ids)}")
        return "\n".join(lines)
    if isinstance(value, str):
        return value
    return ""


def _format_internal_link_target(value: object) -> str:
    """internal_link_target({"type","content_id","path"} dict)을 자연어 안내문으로 변환."""
    if isinstance(value, dict):
        path = str(value.get("path") or "").strip()
        return f"본문에서 자연스러운 위치에 내부 링크로 연결: {path}" if path else ""
    if isinstance(value, str):
        return value
    return ""


_SAME_AS_PHILOSOPHY = "- (위 [승인된 콘텐츠 운영 기준]과 동일)"


def _brief_safety_bullets(
    brief_values: object, philosophy_values: list[str] | None
) -> str:
    """브리프의 안전/메시지 필드를 중복 없이 렌더링.

    브리프는 대부분 철학에서 그대로 파생되므로(build_content_brief) 같은 목록이
    한 프롬프트 안에 두 번 실린다. 플랫폼 공통 규칙은 정적 시스템 블록에 이미 있고,
    철학과 바이트 단위로 같은 목록은 참조 한 줄로 대체한다. 브리프에만 있는 값은
    그대로 남겨 운영자가 슬롯 단위로 덧붙인 지시가 사라지지 않게 한다.
    """
    values = [
        str(value)
        for value in (brief_values or [])
        if str(value).strip() and str(value) not in _MANDATORY_SAFETY_ENTRIES
    ]
    if not values:
        return _bullet_list([])
    if philosophy_values is not None and values == list(philosophy_values):
        return _SAME_AS_PHILOSOPHY
    return _bullet_list(values)


def _build_content_brief_context(
    content_brief: dict | None,
    philosophy: HospitalContentPhilosophy | None = None,
) -> str:
    if not content_brief:
        return ""

    philosophy_must_use: list[str] | None = None
    philosophy_avoid: list[str] | None = None
    philosophy_risk: list[str] | None = None
    if philosophy is not None:
        hospital_safety = _hospital_specific_safety(philosophy)
        philosophy_must_use = [
            str(value)
            for value in (getattr(philosophy, "must_use_messages", None) or [])
            if str(value).strip()
        ]
        philosophy_avoid = hospital_safety["avoid_messages"]
        philosophy_risk = hospital_safety["medical_ad_risk_rules"]

    return f"""
[승인된 콘텐츠 가이드]
target_query: {content_brief.get('target_query') or ''}
patient_intent: {content_brief.get('patient_intent') or ''}
treatment_narrative: {_format_treatment_narrative(content_brief.get('treatment_narrative'))}
must_use_messages:
{_brief_safety_bullets(content_brief.get('must_use_messages'), philosophy_must_use)}
avoid_messages:
{_brief_safety_bullets(content_brief.get('avoid_messages'), philosophy_avoid)}
medical_risk_rules:
{_brief_safety_bullets(content_brief.get('medical_risk_rules'), philosophy_risk)}
internal_link_target: {_format_internal_link_target(content_brief.get('internal_link_target'))}
operator_notes:
{_bullet_list(content_brief.get('operator_notes') or [])}
planned_publish_date: {content_brief.get('planned_publish_date') or ''}
""".strip()


def _usage_token(usage: object, field: str) -> int:
    """usage 필드를 방어적으로 정수로 읽는다.

    SDK는 캐시를 쓰지 않은 응답에서 cache_* 필드를 None으로 돌려주고, 테스트 더블은
    아예 usage가 없을 수 있다. 둘 다 0으로 접는다.
    """
    try:
        return max(0, int(getattr(usage, field, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _bullet_list(values: list) -> str:
    return "\n".join(f"- {value}" for value in values if value) or "- 없음"


def _curated_reference_focus(content_brief: dict | None, result: dict | None = None) -> str:
    """Return only the intended topic text used for curated source matching.

    Matching against the whole generated body is unsafe: a breast-ultrasound article
    that merely mentions cancer screening can otherwise be overwritten with a
    colorectal-cancer reference.  The approved target query, treatment narrative,
    and generated heading define the topic; incidental body phrases must not change
    its evidence set.

    병원 단위로 승인된 `must_use_messages`도 같은 이유로 여기 들어오지 않는다
    (`_hospital_wide_reference_focus` 참고).
    """
    values: list[object] = []
    if content_brief:
        query_target = content_brief.get("query_target")
        treatment_narrative = content_brief.get("treatment_narrative")
        values.extend(
            [
                content_brief.get("target_query"),
                content_brief.get("target_keyword"),
                query_target.get("name") if isinstance(query_target, dict) else None,
                treatment_narrative.get("treatment")
                if isinstance(treatment_narrative, dict)
                else treatment_narrative,
                treatment_narrative.get("angle")
                if isinstance(treatment_narrative, dict)
                else None,
            ]
        )
    if result:
        values.extend([result.get("title"), result.get("faq_question")])
    return " ".join(str(value) for value in values if value)


def _hospital_wide_reference_focus(content_brief: dict | None) -> str:
    """Approved hospital-wide messaging, usable only when the slot names no topic.

    `must_use_messages`는 병원마다 한 벌이고 그 병원의 모든 글에 같이 실린다. 그 문장이
    이 글과 다른 질환·시술을 말하면(간 질환 글을 쓰는 병원의 승인 문구에 "대장내시경·
    용종절제"가 있는 식) 카탈로그는 그 질환의 검증된 문서를 고르고, 작가는 그것을
    "현재 주제와 일치하는 검증된 문서 — 이 URL만 인용"으로 받는다. 큐레이션 URL은 주제
    불일치 제거를 면제받으므로 그 근거는 끝까지 남아 독립 검수의 REFERENCE 지적
    (CONTENT_AI_HARD_FINDING)이 된다. 주제 불일치 제거(`_article_topic_terms`)가 병원
    단위 문구를 글의 주제로 보지 않는 것과 같은 이유로, 선택에서도 이 문구는 글 자신의
    주제가 없을 때의 마지막 단서일 뿐이다.
    """

    if not content_brief:
        return ""
    return " ".join(
        str(message)
        for message in (content_brief.get("must_use_messages") or [])
        if message
    )


def _topic_aligned_curated_sources(
    content_brief: dict | None, result: dict | None = None
) -> list[dict[str, str]]:
    """Curated documents for *this article's* topic, not for the hospital at large."""

    sources = select_curated_authority_sources(
        _curated_reference_focus(content_brief, result)
    )
    if sources:
        return sources
    if normalize_topic_text((content_brief or {}).get("target_keyword")):
        # 이 슬롯은 다룰 임상 키워드를 스스로 갖고 있다. 카탈로그에 그 주제의 문서가
        # 없다는 뜻이므로, 병원의 다른 진료 문구로 근거를 대신 채우지 않는다. 빈 결과는
        # 기존 GEO 하드 거절 → 재작성 경로로 가서 작가가 주제에 맞는 출처를 찾는다.
        return []
    return select_curated_authority_sources(
        _hospital_wide_reference_focus(content_brief)
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    # tenacity 는 **전송·공급자 오류 전용**이다. 우리 검증기가 던지는 ValueError 는
    # 같은 프롬프트로 다시 사면 같은 결과가 나오는 결정적 실패이므로 여기서 재시도하지
    # 않고, generate_content 의 재작성 루프가 실패 사유를 작가에게 넘겨 다시 쓰게 한다
    # (잘림·JSON 파싱·분량·가격·SEO·GEO·FAQ·금지 표현 전부 ValueError 계열).
    #
    # NON_RETRYABLE_LLM_ERRORS: a malformed request, a bad key, a revoked
    # permission or a missing model does not become valid by waiting.  Retrying
    # them burned three request slots and up to 12s of backoff per item before
    # surfacing the same error.  Rate limits (429), 5xx and timeouts stay
    # retryable.
    retry=retry_if_not_exception_type((ValueError, *NON_RETRYABLE_LLM_ERRORS)),
    reraise=True,
)
async def _generate_content_attempt(
    hospital: Hospital,
    content_type: ContentType,
    existing_titles: list[str] | None = None,
    philosophy: HospitalContentPhilosophy | None = None,
    content_brief: dict | None = None,
    remediation_findings: list[str] | None = None,
    _attempt_context: dict | None = None,
) -> dict:
    """
    Claude Sonnet으로 콘텐츠 생성.
    Returns: {"title": str, "body": str, "meta_description": str}
    """
    import asyncio

    profile_ctx = _build_profile_context(hospital)
    philosophy_ctx = _build_philosophy_context(philosophy)
    brief_ctx = _build_content_brief_context(content_brief, philosophy)
    type_prompt = _fill_type_prompt(content_type, hospital, content_brief)

    avoid_titles = ""
    if existing_titles:
        # 상한을 한 번 더 여기서 건다. 호출자(tasks.py)가 이미 LIMIT을 걸지만,
        # 다른 경로나 테스트가 긴 목록을 넘겨도 프롬프트가 부풀지 않게 한다.
        recent_titles = list(existing_titles)[:EXISTING_TITLE_PROMPT_LIMIT]
        avoid_titles = "\n\n최근 발행 제목 일부 (중복 금지):\n" + "\n".join(
            f"- {t}" for t in recent_titles
        )
        # 같은 키워드를 다시 다루는 회차는 사후 중복 가드에 걸리기 쉬우므로 미리 각도를
        # 다르게 잡도록 요구한다. 키워드 자체는 그대로 유지해야 측정 질의에 답한다.
        keyword = normalize_topic_text((content_brief or {}).get("target_keyword"))
        if keyword and any(
            keyword in normalize_topic_text(title) for title in recent_titles
        ):
            avoid_titles += (
                "\n위 제목과 같은 핵심 키워드를 다루더라도 질문·관점·독자 상황을 다르게 "
                "잡아 서로 다른 글이 되게 하세요(제목이 비슷해지면 안 됩니다)."
            )

    brief_context = f"\n\n{brief_ctx}" if brief_ctx else ""
    curated_candidates = _topic_aligned_curated_sources(content_brief)
    curated_candidate_hint = ""
    if curated_candidates:
        rendered_candidates = "\n".join(
            f"- {reference['title']}: {reference['url']}"
            for reference in curated_candidates
        )
        curated_candidate_hint = (
            "\n\n[현재 주제와 일치하는 검증된 문서 — 해당 주장을 쓸 때 이 URL만 인용]\n"
            f"{rendered_candidates}"
        )
    remediation_context = _build_remediation_context(remediation_findings)

    # 프롬프트 캐시 배치. 접두어 일치 방식이라 "고정 → 병원 단위 고정 → 아이템 단위 변동"
    # 순서를 지켜야 한다.
    #   블록 1 (STATIC_SYSTEM_BLOCK): 작성 규칙 + 금지 표현 + 출처 화이트리스트.
    #     모든 병원·모든 아이템에서 동일 → 여기서 캐시 breakpoint.
    #   블록 2 (프로파일 + 운영 기준): 병원마다 다르지만 같은 병원의 월간 배치 안에서는
    #     동일 → 두 번째 breakpoint. 한 병원의 12~20편이 이 블록을 재사용한다.
    #   user 메시지: 유형 프롬프트·브리프·최근 제목·큐레이션 후보·재작성 지적처럼
    #     아이템마다 바뀌는 것만. 여기에 변동 값을 몰아둬야 위 두 블록이 살아남는다.
    hospital_system_block = profile_ctx
    if philosophy_ctx:
        hospital_system_block = f"{profile_ctx}\n\n{philosophy_ctx}"
    system_blocks = [
        {
            "type": "text",
            "text": STATIC_SYSTEM_BLOCK,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": hospital_system_block,
            "cache_control": {"type": "ephemeral"},
        },
    ]
    user_message = (
        f"{brief_context}\n\n"
        f"{type_prompt}{avoid_titles}{curated_candidate_hint}{remediation_context}"
    ).strip()

    # 실제 공급자 호출 계수. 이 함수는 tenacity로 최대 3회 재시도되고 OpenRouter 클라이언트는
    # max_retries=0이라, 본문 1회 실행 = HTTP 요청 1회다. 여기서 세지 않으면 비용 화면의
    # '예약'과 '실제'가 최대 3배까지 벌어져도 드러나지 않는다.
    from app.services import cost_guard

    await cost_guard.record_provider_call("content")

    # asyncio에서 sync OpenAI-호환(OpenRouter) 클라이언트 호출
    loop = asyncio.get_running_loop()
    from app.services import provider_usage

    attempt_context = _attempt_context if _attempt_context is not None else {}
    logical_call_id = str(attempt_context.setdefault("logical_call_id", uuid.uuid4()))
    http_attempt = int(attempt_context.get("http_attempt") or 0) + 1
    attempt_context["http_attempt"] = http_attempt
    attempt_id = f"{logical_call_id}:http:{http_attempt}"

    try:
        response = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=12000,
                messages=[
                    openrouter.system_message(system_blocks),
                    {"role": "user", "content": user_message},
                ],
                tools=[
                    openrouter.function_tool(
                        name=ARTICLE_TOOL_NAME,
                        description=ARTICLE_TOOL["description"],
                        input_schema=_article_tool_schema(content_type),
                    )
                ],
                tool_choice=openrouter.forced_tool_choice(ARTICLE_TOOL_NAME),
            ),
        )
    except Exception:
        await provider_usage.record_attempt(
            provider="openrouter",
            model=settings.CLAUDE_MODEL,
            workflow="content_generation",
            cost_category="content",
            hospital_id=getattr(hospital, "id", None),
            logical_call_id=logical_call_id,
            attempt_id=attempt_id,
            http_attempt=http_attempt,
            usage_known=False,
        )
        raise

    usage = getattr(response, "usage", None)
    # OpenRouter(OpenAI 형태)는 캐시 적중을 prompt_tokens 안에 포함하고 세부는
    # prompt_tokens_details.cached_tokens에 둔다. 적중량만 별도 로그로 관찰한다.
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cache_creation_tokens = 0
    cache_read_tokens = _usage_token(prompt_details, "cached_tokens")
    uncached_input_tokens = _usage_token(usage, "prompt_tokens")
    logger.info(
        "content generation usage: hospital=%s type=%s input=%d "
        "cache_creation=%d cache_read=%d output=%d",
        getattr(hospital, "id", None),
        getattr(content_type, "value", content_type),
        uncached_input_tokens,
        cache_creation_tokens,
        cache_read_tokens,
        _usage_token(usage, "completion_tokens"),
    )
    await provider_usage.record_attempt(
        provider="openrouter",
        model=settings.CLAUDE_MODEL,
        workflow="content_generation",
        cost_category="content",
        hospital_id=getattr(hospital, "id", None),
        logical_call_id=logical_call_id,
        attempt_id=attempt_id,
        http_attempt=http_attempt,
        provider_request_id=str(getattr(response, "id", "") or "") or None,
        usage=usage,
    )

    # 잘린 응답은 JSON 파싱 실패로만 드러나 "가격·지역·검색 구조 게이트 실패"라는 틀린
    # 원인으로 기록됐다. 사용량 기록(위)은 이미 끝났으므로 여기서 전용 오류로 끊는다.
    stop_reason = llm_structured_output.incomplete_reason(response)
    if stop_reason is not None:
        raise TruncatedProviderOutputError(
            f"Provider output was truncated before completion (stop_reason={stop_reason}) "
            "— 응답이 끝까지 완성되지 않았습니다. 본문 분량을 순수 글자 수 2,400~3,200자로 "
            "줄이고 JSON 객체를 끝까지 닫아 다시 작성하세요."
        )

    result = _extract_generated_result(response)

    _validate_body_length(result.get("body"))
    _validate_unverified_price_claims(result.get("body"))

    # 참고 자료 정규화를 GEO 검증보다 먼저 수행한다. GEO hard-fail은
    # "references가 비어있으면 재시도"인데, raw(정규화 전) 리스트로 검사하면
    # 화이트리스트 밖 URL만 인용된 경우 hard-fail은 통과하고 최종 references는
    # 빈 배열로 저장돼 근거 없이 발행이 완료된다. 정규화된 리스트로 검사해야
    # tenacity 재시도가 "화이트리스트 통과 references 1개 이상"을 실제로 강제한다.
    raw_references = result.get("references")
    result["references"] = _normalize_references(raw_references)
    # 제거는 단계마다 조용히 일어난다(logger.info). 그 사실이 작가에게 닿지 않으면
    # 재작성 회차는 "references is empty"만 보고 같은 URL을 다시 낸다.
    reference_drops = _reference_drop_notes(
        raw_references, result["references"], _REFERENCE_DROP_NOT_CITABLE
    )
    page_titles: dict[str, str] = {}
    if settings.APP_ENV == "production" and result["references"]:
        fetched_from = result["references"]
        result["references"], page_titles = await _drop_definitively_broken_references(
            fetched_from, with_titles=True
        )
        reference_drops += _reference_drop_notes(
            fetched_from, result["references"], _REFERENCE_DROP_BROKEN
        )
    if result["references"]:
        # 주제와 어긋나는 근거는 거절 사유가 아니라 제거 대상이다. 비면 아래 GEO 게이트가
        # 기존대로 MissingCitableReferencesError → 큐레이션 치유 경로로 보낸다.
        scored_from = result["references"]
        result["references"] = _drop_unrelated_references(
            scored_from, result, content_brief, page_titles
        )
        reference_drops += _reference_drop_notes(
            scored_from, result["references"], _REFERENCE_DROP_UNRELATED
        )

    return _validate_generated_result(
        result,
        hospital,
        content_type,
        content_brief,
        reference_drop_notes=reference_drops,
    )


def _reference_title_violations(title: str) -> list[str]:
    """참고자료 제목 한 건을 공개 표면 기준(평문)으로 검사한다."""
    if not title:
        return []
    return check_forbidden_content_fields(
        {REFERENCE_TITLES_FIELD: title}, (REFERENCE_TITLES_FIELD,)
    )


def _sanitize_reference_titles(references: object) -> object:
    """광고 문구 제목을 기관 표기로 바꾼다 — 근거는 지키고 노출만 없앤다.

    참고자료의 URL만 화이트리스트 검증을 거치고 제목은 모델 자유 텍스트다. 공신력
    문서에 "부작용 없는 치료 안내" 같은 제목이 붙으면 그 제목이 병원 페이지와
    JSON-LD citation.name 으로 그대로 나간다. 제목을 기관 이름으로 바꾸면 공급자를
    다시 부르지 않고 결정적으로 고칠 수 있다.

    화이트리스트 밖 URL은 이 단계 이전에 _normalize_references 가 이미 떨궈낸다.
    (여기서 고칠 수 없는 제목은 그대로 두고 아래 공통 금지 표현 게이트가 글 전체를
    폐기한다 — 우회 경로를 만들지 않는다.)
    """
    if not isinstance(references, list):
        return references
    sanitized: list = []
    for reference in references:
        if not isinstance(reference, dict):
            sanitized.append(reference)
            continue
        title = str(reference.get("title") or "").strip()
        url = str(reference.get("url") or "").strip()
        if not _reference_title_violations(title):
            sanitized.append(reference)
            continue
        label = institution_label_for_url(url)
        if not label or _reference_title_violations(label):
            sanitized.append(reference)
            continue
        logger.info(
            "Replacing an unsafe reference title with the institution label: host=%s",
            urlparse(url).hostname,
        )
        sanitized.append({**reference, "title": label})
    return sanitized


def _validate_generated_result(
    result: dict,
    hospital: Hospital,
    content_type: ContentType,
    content_brief: dict | None,
    *,
    reference_drop_notes: list[str] | None = None,
) -> dict:
    """Apply every stored-content hard gate to one normalized provider result."""

    # FAQPage JSON-LD fields are a hard type contract, not optional decoration.
    # Conversely, non-FAQ outputs must not leak model-supplied FAQ schema fields.
    result["faq_question"] = _trim_or_none(result.get("faq_question"), 300)
    result["faq_answer_summary"] = _trim_or_none(result.get("faq_answer_summary"), 600)
    if content_type == ContentType.FAQ:
        if not result["faq_question"] or not result["faq_answer_summary"]:
            raise ValueError("FAQ output requires faq_question and faq_answer_summary")
        if not result["faq_question"].endswith("?"):
            raise ValueError("FAQ question must end with a question mark")
    else:
        result["faq_question"] = None
        result["faq_answer_summary"] = None

    # ── SEO/GEO 검증 ──────────────────────────────────────────────
    seo_findings = _validate_seo(result, hospital, content_brief, content_type)
    geo_findings = _validate_geo(
        result, hospital, content_type, reference_drop_notes=reference_drop_notes
    )
    # 측정 질의 대응 검사. 워커가 이 목록을 보고 한 번만 보완 재작성을 돌린다.
    target_findings = _validate_target_alignment(result, content_brief, content_type)
    result["target_alignment_findings"] = target_findings

    # 세 검증에서 나온 SOFT 결과를 result에 첨부 — AE 화면이 참조할 수 있도록
    all_findings = seo_findings + geo_findings + target_findings
    result["seo_geo_findings"] = all_findings
    result["seo_geo_score"] = max(0, 100 - len(all_findings) * 10)
    if all_findings:
        logger.info(
            "SEO/GEO soft findings (%d): %s",
            len(all_findings),
            "; ".join(all_findings),
        )

    # 금지 표현이 하나라도 있으면 이 결과는 폐기한다. 문장 일부를 삭제·치환하면 문법과
    # 의료 의미가 달라질 수 있으므로, tenacity가 공급자를 다시 호출해 공개 필드 전체가
    # 완전한 새 응답인 결과만 반환하게 한다. 재시도 소진 시 호출자는 결과를 저장하지 않고
    # 기존 생성 실패 incident/outbox 경로를 연다.
    violations = check_forbidden_content_fields(result, FORBIDDEN_CHECK_FIELDS)
    # 모델이 붙인 제목을 먼저 기관 표기로 고쳐 본 뒤, **모든** 제목을 예외 없이
    # 검사한다. 발행·공개 게이트도 같은 기준으로 모든 제목을 다시 검사한다(방어 심층).
    result["references"] = _sanitize_reference_titles(result.get("references"))
    reference_titles = " ".join(
        str(reference.get("title") or "").strip()
        for reference in (result.get("references") or [])
        if isinstance(reference, dict)
    )
    violations.extend(
        check_forbidden_content_fields(
            {REFERENCE_TITLES_FIELD: reference_titles}, (REFERENCE_TITLES_FIELD,)
        )
    )
    if violations:
        logger.warning(
            "Forbidden expressions found labels=%s — discarding the complete response and "
            "regenerating all public fields",
            ",".join(violations),
        )
        raise ValueError(f"Forbidden medical expressions require complete regeneration: {violations}")

    # references는 GEO 검증 전에 이미 정규화됨(list[{title,url,source_type}]) — 중복 정규화 불필요.

    # meta_description 컬럼은 VARCHAR(300) — 프롬프트는 100~150자를 요구하지만 모델 출력은
    # 보장이 없고, 300자 초과 시 야간 배치의 per-item 커밋이 DataError로 실패한다.
    result["meta_description"] = _trim_or_none(result.get("meta_description"), 300)

    return result


# 엔지니어 로그에 남기는 실패 상세의 길이 상한. 우리 검증기의 메시지는 모두 이보다 짧고,
# 상한은 예기치 못한 외부 예외가 로그를 덮는 것만 막는다.
GENERATION_FAILURE_DETAIL_MAX_CHARS = 400


def generation_failure_detail(error: BaseException) -> str:
    """Render one generation failure for engineer-facing logs.

    운영자에게 보이는 문구(`classify_generation_failure`)는 허용 목록으로 좁혀져 있어
    어느 게이트가 걸렸는지 말하지 않는다. 로그까지 예외 이름만 남기면 `error=ValueError`만
    되풀이되고 실제 사유는 어디에도 남지 않아, 거절을 코드로도 로그로도 좁힐 수 없다.
    여기서는 메시지를 그대로 남기되 길이만 묶는다. 이 값은 로그 전용이며 운영자 문구·
    인시던트·Slack에 쓰지 않는다.
    """

    message = " ".join(str(error).split())
    if not message:
        return type(error).__name__
    return f"{type(error).__name__}: {message[:GENERATION_FAILURE_DETAIL_MAX_CHARS]}"


async def generate_content(
    hospital: Hospital,
    content_type: ContentType,
    existing_titles: list[str] | None = None,
    philosophy: HospitalContentPhilosophy | None = None,
    content_brief: dict | None = None,
    remediation_findings: list[str] | None = None,
) -> dict:
    """Generate with bounded, *informed* rewrites and apply deterministic heals.

    결정적 검증기(분량·가격·SEO·GEO·FAQ·금지 표현·잘림)의 실패는 같은 프롬프트로
    다시 사도 같은 결과가 나온다. 그래서 tenacity 의 blind 재시도 대신 여기서
    직전 실패 사유를 `remediation_findings` 로 작가에게 넘겨 다시 쓰게 한다.
    총 공급자 호출 수는 종전(tenacity 3회)과 같고, 전송 오류 재시도까지 합쳐도
    GENERATION_PROVIDER_CALL_BUDGET 을 넘지 않는다.
    """

    # 한 번의 generate_content = 하나의 논리 호출. 재작성 회차와 전송 재시도가 모두
    # 이 lineage 아래 HTTP attempt 로 기록돼 '예약'과 '실제'가 벌어지지 않는다.
    attempt_context = {"logical_call_id": str(uuid.uuid4()), "http_attempt": 0}
    caller_findings = [
        str(finding) for finding in (remediation_findings or []) if str(finding).strip()
    ]
    findings = list(caller_findings)
    # 회차마다 걸리는 게이트가 달라진다. 지적을 마지막 사유로 덮어쓰면 작가는 이번 지적만
    # 보고 직전 회차에 통과했던 조건을 놓친다 — 빈 references를 채우면 분량이 하한 아래로
    # 내려가고, 분량을 늘리면 다시 references가 비는 왕복이 그렇게 생긴다. 지금까지 본
    # 결정적 거절을 모두 지니고 간다.
    validator_findings: list[str] = []
    last_error: ValueError | None = None

    for _round in range(GENERATION_REMEDIATION_ROUNDS):
        if int(attempt_context.get("http_attempt") or 0) >= GENERATION_PROVIDER_CALL_BUDGET:
            break
        try:
            return await _generate_content_attempt(
                hospital,
                content_type,
                existing_titles,
                philosophy,
                content_brief,
                findings,
                attempt_context,
            )
        except MissingCitableReferencesError as exc:
            last_error = exc
            try:
                healed = _heal_from_curated_catalog(
                    exc, hospital, content_type, content_brief
                )
            except ValueError as heal_error:
                # 큐레이션 근거를 붙였더니 다른 게이트가 걸렸다. 그 사유를 그대로
                # 다음 회차에 넘긴다 — 이미 결제한 응답을 여기서 버리지 않는다.
                last_error = heal_error
            else:
                if healed is not None:
                    return healed
        except DirectorNameMissingError as exc:
            # 승인된 원장명 한 줄은 결정적으로 붙일 수 있다. 본문이 그 한 가지만
            # 빼고 완전한데도 재작성을 두 번 더 사면 정상 글 한 편에 세 번 결제한다.
            last_error = exc
            try:
                healed = _heal_missing_director_name(
                    exc, hospital, content_type, content_brief
                )
            except ValueError as heal_error:
                # 이름을 붙였더니 다른 게이트가 걸렸다 — 그 사유로 다시 쓰게 한다.
                last_error = heal_error
            else:
                if healed is not None:
                    return healed
        except ValueError as exc:
            last_error = exc
        logger.info(
            "content generation rejected deterministically; feeding the finding back: "
            "hospital=%s type=%s error=%s",
            getattr(hospital, "id", None),
            getattr(content_type, "value", content_type),
            generation_failure_detail(last_error),
        )
        validator_findings = _validator_remediation_findings(
            last_error, validator_findings
        )
        findings = [*validator_findings, *caller_findings][
            :GENERATION_REMEDIATION_FINDING_LIMIT
        ]

    if last_error is None:  # pragma: no cover - 루프는 최소 1회 실행된다
        raise RuntimeError("content generation ended without a result or an error")
    raise last_error


def _validator_remediation_findings(
    error: ValueError, previous_findings: list[str]
) -> list[str]:
    """Accumulate deterministic rejections so one rewrite satisfies all of them.

    이번 회차 사유를 맨 앞에 두고 앞선 회차의 사유를 뒤에 남긴다. 게이트는 순서대로
    걸리므로(분량 → 가격 → SEO → GEO) 한 회차의 지적만 넘기면 그 하나를 고치는 동안
    이미 통과했던 조건이 다시 깨진다 — 실제로 빈 references를 채운 회차가 분량을
    1,800자 하한 아래로 떨어뜨렸다. 같은 사유가 반복되면 한 번만 남긴다.
    """
    message = " ".join(str(error).split())[:240]
    finding = (
        "직전 응답이 시스템 검증에서 거부되었습니다. 아래 지적을 **모두** 해소해 전체를 "
        f"다시 작성하세요(하나를 고치며 다른 하나를 깨뜨리면 같은 거절이 반복됩니다): {message}"
    )
    accumulated = [finding]
    for previous in previous_findings:
        if previous not in accumulated:
            accumulated.append(previous)
    return accumulated[:GENERATION_REMEDIATION_FINDING_LIMIT]


def _heal_from_curated_catalog(
    error: MissingCitableReferencesError,
    hospital: Hospital,
    content_type: ContentType,
    content_brief: dict | None,
) -> dict | None:
    """Recover an empty reference list from the human-verified catalog, or give up.

    Preserve any citable provider reference; only an actually empty list may be
    healed from the topic-specific curated documents.
    """
    result = error.result
    if result.get("references"):
        return None
    curated_references = _topic_aligned_curated_sources(content_brief, result)
    if not curated_references:
        return None
    result["references"] = curated_references
    return _validate_generated_result(result, hospital, content_type, content_brief)


def _heal_missing_director_name(
    error: DirectorNameMissingError,
    hospital: Hospital,
    content_type: ContentType,
    content_brief: dict | None,
) -> dict | None:
    """Append the approved director name once, in the same round, or give up.

    이름 누락은 승인된 프로파일 값 한 줄로 결정적으로 고쳐진다. 다시 사도 결과가
    나아진다는 보장이 없으므로 공급자를 한 번 더 부르지 않는다. 이름이 없거나 이미
    본문에 있으면(=다른 이유로 실패한 것이면) None을 돌려 재작성 루프로 보낸다.
    """
    result = error.result
    director = (hospital.director_name or "").strip()
    body = (result.get("body") or "").rstrip()
    if not director or director in body:
        return None
    result["body"] = f"{body}\n\n{hospital.name}의 원장은 {director}입니다."
    return _validate_generated_result(result, hospital, content_type, content_brief)


# Keep the transport retry controller observable/configurable at the public seam used by
# tests and provider-call metering. It now governs **only** transport/provider errors —
# 결정적 검증 실패의 재작성 예산은 GENERATION_REMEDIATION_ROUNDS 가 가진다.
generate_content.retry = _generate_content_attempt.retry


def _build_remediation_context(findings: list[str] | None) -> str:
    """Render bounded validator feedback for one automatic rewrite attempt.

    지적은 우리 검수기·독립 검수가 남긴 수정 요구이므로 작가가 실제로 수행해야 한다.
    보호해야 하는 것은 명령 자체가 아니라 **사실의 출처**다 — 지적 안에 인용된 본문·
    수치·URL은 직전 결과에서 따온 것일 뿐 승인된 병원 정보가 아니다. 프롬프트에 닿는
    분량은 종전대로 항목 수와 길이로 묶는다.
    """

    normalized = [
        " ".join(str(finding).split())[:240]
        for finding in (findings or [])
        if str(finding).strip()
    ][:GENERATION_REMEDIATION_FINDING_LIMIT]
    if not normalized:
        return ""
    bullets = "\n".join(f"- {finding}" for finding in normalized)
    return (
        "\n\n[직전 자동 검수 결과 — 이번 회차에 반드시 반영할 수정 요구]\n"
        "아래는 직전 결과를 검수한 시스템이 남긴 수정 요구입니다. 각 항목을 모두 "
        "해소하고, 승인된 병원 정보와 작성 규칙을 유지하면서 글 전체를 다시 작성하세요. "
        "다만 항목 안에 인용된 문장·수치·URL은 직전 결과에서 따온 것일 뿐 새로 승인된 "
        "병원 사실이 아니므로 그것을 근거로 새 주장을 만들지 마세요.\n"
        # 지적 중에는 "그 주장을 삭제하거나 완화하라"가 많다. 문장을 덜어내는 것만으로
        # 회차를 끝내면 본문이 저장 하한 아래로 내려가 분량 거절로 바뀐다.
        "지적된 주장을 삭제하거나 완화했다면 남은 절의 설명을 보강해 순수 글자 수 "
        f"{CONTENT_BODY_TARGET_MIN_CHARS:,}~{CONTENT_BODY_TARGET_MAX_CHARS:,}자를 "
        f"유지하세요(순수 글자 수 {CONTENT_BODY_MIN_CHARS:,}자 미만은 저장되지 않습니다).\n"
        f"{bullets}"
    )


# 참고자료 제목을 담아 검사하는 가상 필드 이름. 발행 게이트가 같은 이름을 쓴다.
REFERENCE_TITLES_FIELD = "reference_titles"

# 의료광고 금지 표현 검사 대상 필드 — 공개 표면(FAQPage rich result 포함)에 노출되는
# 모든 텍스트 필드를 빠짐없이 포함해야 한다 (P1-2: FAQ 필드 누락 회귀 방지).
FORBIDDEN_CHECK_FIELDS = (
    "title",
    "body",
    "meta_description",
    "faq_question",
    "faq_answer_summary",
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


def _extract_generated_result(response: object) -> dict:
    """Prefer the forced tool call; fall back to the legacy text transport.

    강제 도구 호출이 정상 경로다. `tool_use.input`은 공급자가 이미 파싱해 보낸
    dict이므로 fence나 본문 안의 큰따옴표가 파싱을 깨뜨릴 수 없다. 텍스트 경로는
    도구를 쓰지 않는 예전 응답(과 기존 테스트)만을 위한 보루이며, 여기서도
    fence 제거까지만 하고 따옴표를 추측해 고치지는 않는다.
    """

    tool_input = llm_structured_output.tool_use_input(response, tool_name=ARTICLE_TOOL_NAME)
    if tool_input is not None:
        return tool_input
    return _parse_json_response(llm_structured_output.first_text(response), json_module=json)


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
        # 이 메시지는 재작성 회차에 작가가 읽는 유일한 지적이다(_validator_remediation_findings).
        # 숫자만 남기면 작가는 화면에 보이는 길이로 세어 몇 문장만 덧붙이고, 평문 기준으로는
        # 여전히 하한 아래에 머문다. 단위와 목표 구간, 늘리는 방법까지 함께 말한다.
        raise ValueError(
            f"Generated content body is too short "
            f"({body_length} < {CONTENT_BODY_MIN_CHARS}) — 공백·마크다운을 제외한 순수 "
            "글자 수 기준입니다(화면에 보이는 길이는 20~35% 더 깁니다). 기존 H2 구조를 "
            "유지한 채 각 절의 설명을 늘려 순수 글자 수 "
            f"{CONTENT_BODY_TARGET_MIN_CHARS:,}~{CONTENT_BODY_TARGET_MAX_CHARS:,}자로 "
            "다시 작성하세요."
        )
    if body_length > CONTENT_BODY_MAX_CHARS:
        raise ValueError(
            f"Generated content body is too long "
            f"({body_length} > {CONTENT_BODY_MAX_CHARS})"
        )


def _validate_unverified_price_claims(value: object) -> None:
    """Reject precise medical pricing/coverage claims not backed by hospital data."""

    if not isinstance(value, str):
        return
    compact = re.sub(r"\s+", " ", value)
    for pattern in UNVERIFIED_PRICE_PATTERNS:
        match = pattern.search(compact)
        if match:
            raise ValueError(
                "Generated content contains an unverified fixed price or coverage claim: "
                f"{match.group(0)[:80]}"
            )

    sentence_bounded = re.sub(
        r"[^\S\n]+", " ", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    # 공적 검진 예외는 같은 문장/창이 아니라 같은 무료·무상 토큰에만 붙는다.
    public_screening_free_spans = {
        (claim.start() + term.start(), claim.start() + term.end())
        for claim in _PUBLIC_SCREENING_FREE_CLAIM_PATTERN.finditer(sentence_bounded)
        for term in _FREE_TERM_PATTERN.finditer(claim.group(0))
    }
    for pattern in (_HOSPITAL_SELF_FREE_PATTERN, _HOSPITAL_CARE_FREE_CLAIM_PATTERN):
        for match in pattern.finditer(sentence_bounded):
            match_free_spans = {
                (match.start() + term.start(), match.start() + term.end())
                for term in _FREE_TERM_PATTERN.finditer(match.group(0))
            }
            if match_free_spans and match_free_spans <= public_screening_free_spans:
                continue
            raise ValueError(
                "Generated content contains an unverified fixed price or coverage claim: "
                f"{match.group(0)[:80]}"
            )


def _validate_seo(
    result: dict, hospital: Hospital, content_brief: dict | None, content_type: ContentType
) -> list[str]:
    """SEO 구조 검증 — HARD-FAIL은 ValueError를 발생시키고 SOFT 결과는 list[str]로 반환.

    HARD (ValueError → tenacity 재시도):
      • 본문에 # H1 헤딩이 있음 — 페이지 제목과 중복되어 문서 구조를 깨뜨림
      • 본문에 ## H2 헤딩이 SEO_H2_MIN(2)개 미만 — 단, NOTICE/FAQ는 짧은 단문이 정상이라 제외(soft)

    SOFT (반환 리스트에 추가, 생성 결과는 살림):
      • primary keyword가 본문에 정확히 일치하지 않을 때 (한국어 형태 변화로 오탐 위험 → soft)
      • title 길이 > SEO_TITLE_MAX_CHARS
      • meta_description 길이가 SEO_META_MIN~MAX 범위 밖
      • 본문에 markdown table(|) 또는 번호/불릿 목록이 없을 때
      • primary keyword가 title 또는 첫 H2에 없을 때
    """
    body: str = result.get("body") or ""
    title: str = result.get("title") or ""
    meta: str = result.get("meta_description") or ""
    findings: list[str] = []

    planned_date = str((content_brief or {}).get("planned_publish_date") or "")
    if planned_date:
        try:
            planned_month = int(planned_date[5:7])
        except (ValueError, IndexError):
            planned_month = 0
        for season, months in SEASON_MONTHS.items():
            if season in title and planned_month and planned_month not in months:
                findings.append(
                    f"{SEASON_MISMATCH_FINDING_PREFIX}: title season '{season}' does not "
                    f"match planned_publish_date {planned_date}"
                )

    if re.search(r"^#\s+\S", body, flags=re.MULTILINE):
        raise ValueError("SEO hard-fail: body must not contain an H1 heading")

    # ── H2 헤딩 개수 — NOTICE/FAQ는 구조 자유라 hard-fail 제외(soft) ──
    h2_matches = re.findall(r"^##\s+\S", body, flags=re.MULTILINE)
    if len(h2_matches) < SEO_H2_MIN:
        if content_type in (ContentType.NOTICE, ContentType.FAQ):
            findings.append(f"H2 {len(h2_matches)}개 (NOTICE/FAQ는 구조 자유 — 참고용)")
        else:
            raise ValueError(
                f"SEO hard-fail: body has {len(h2_matches)} H2 heading(s), "
                f"minimum is {SEO_H2_MIN}"
            )

    # ── primary keyword 결정 ────────────────────────────────────────
    # 1순위는 브리프가 분해해 둔 임상 키워드다. 예전에는 target_query의 **첫 토큰**을
    # 썼는데 측정 질의의 첫 토큰은 거의 항상 지역명("강남역")이라, 이 검사가 사실상
    # "지역명이 본문에 있는가"만 재고 있었다(GEO 검증이 이미 하는 일).
    primary_kw: str = ""
    if content_brief:
        primary_kw = str(content_brief.get("target_keyword") or "").strip()
        if not primary_kw:
            tq = (content_brief.get("target_query") or "").strip()
            if tq:
                primary_kw = tq.split()[0]
    if not primary_kw and hospital.keywords:
        primary_kw = (hospital.keywords[0] or "").split()[0]

    # ── SOFT: primary keyword 본문 부재 (한국어 형태 변화로 exact-substring 오탐 위험) ──
    if primary_kw and primary_kw not in body:
        findings.append(
            f"primary keyword '{primary_kw}' 본문에서 정확히 일치하지 않음 (형태 변화 가능)"
        )

    # ── SOFT: keyword in title or first H2 ──────────────────────────
    if primary_kw:
        first_h2_match = re.search(r"^##\s+(.+)$", body, flags=re.MULTILINE)
        first_h2_text = first_h2_match.group(1) if first_h2_match else ""
        if primary_kw not in title and primary_kw not in first_h2_text:
            findings.append(
                f"keyword '{primary_kw}' not in title or first H2 (낮은 prominence)"
            )

    # ── SOFT: title 길이 ────────────────────────────────────────────
    if len(title) > SEO_TITLE_MAX_CHARS:
        findings.append(
            f"title {len(title)}자 > {SEO_TITLE_MAX_CHARS}자 (Google 절단 위험)"
        )

    # ── SOFT: meta_description 길이 ─────────────────────────────────
    meta_len = len(meta)
    if meta_len and not (SEO_META_MIN_CHARS <= meta_len <= SEO_META_MAX_CHARS):
        findings.append(
            f"meta_description {meta_len}자 (권고 {SEO_META_MIN_CHARS}~{SEO_META_MAX_CHARS}자)"
        )

    # ── SOFT: listicle/표 존재 여부 ──────────────────────────────────
    has_table = bool(re.search(r"^\|.+\|", body, flags=re.MULTILINE))
    has_list = bool(re.search(r"^(\d+\.|[-*])\s+\S", body, flags=re.MULTILINE))
    if not (has_table or has_list):
        findings.append("listicle/표 없음 — AI 인용률 저하 가능 (프롬프트 규칙 5 위반)")

    return findings


# 생성 결과가 측정 질의를 실제로 답했는지 보는 검사의 표지 문자열.
# 워커(tasks._generate_with_auto_review)가 이 접두어로 지적을 식별해 **한 번만**
# 보완 재작성을 돌린다. 무한 재시도가 아니다 — 두 번째도 놓치면 글은 살리고 soft
# finding으로 남겨 Admin에서 보이게 한다.
TARGET_KEYWORD_FINDING_PREFIX = "측정 질의 키워드 미반영"


def _validate_target_alignment(
    result: dict, content_brief: dict | None, content_type: ContentType
) -> list[str]:
    """측정 질의의 임상 키워드가 제목·첫 H2·FAQ 질문 중 한 곳에는 있어야 한다.

    본문 어딘가에 한 번 나오는 것으로는 부족하다 — AI가 이 글을 그 질문의 답으로
    고르려면 키워드가 문서의 **주제 위치**에 있어야 한다. 반대로 하드 게이트로
    올리면 한국어 형태 변화(‘허리디스크’ vs ‘허리 디스크’)로 정상 결과가 버려지므로,
    한 번의 보완 재작성까지만 요구하고 그 뒤에는 통과시킨다.
    """
    # 조향 대상이 아닌 유형(COLUMN·HEALTH·NOTICE)은 프롬프트에 키워드를 보여 주지도
    # 않는다. 보여 주지 않은 요구로 보완 재작성 예산을 태우지 않는다.
    if content_type not in TARGET_STEERED_TYPES:
        return []

    brief = content_brief or {}
    keyword = str(brief.get("target_keyword") or "").strip()
    if not keyword:
        return []

    title = str(result.get("title") or "")
    faq_question = str(result.get("faq_question") or "")
    body = str(result.get("body") or "")
    first_h2_match = re.search(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    first_h2 = first_h2_match.group(1) if first_h2_match else ""

    if any(keyword in place for place in (title, first_h2, faq_question)):
        return []

    target_query = str(brief.get("target_query") or "").strip()
    return [
        f"{TARGET_KEYWORD_FINDING_PREFIX}: '{keyword}'이(가) 제목·첫 H2·FAQ 질문 어디에도 "
        f"없습니다. 측정 질의: {target_query or '(없음)'}"
    ]


def _validate_geo(
    result: dict,
    hospital: Hospital,
    content_type: ContentType,
    *,
    reference_drop_notes: list[str] | None = None,
) -> list[str]:
    """GEO 엔티티 공출현 + 증거 신호 검증.

    HARD (ValueError → tenacity 재시도):
      • 의료 안내 유형인데 references가 빈 리스트일 때
      • 병원명·원장명·지역명 중 프로파일에 있는 값이 본문에서 누락됐을 때

    SOFT (반환 리스트에 추가):
      • 숫자/통계 패턴(SEO_STAT_PATTERN)이 body에 없을 때
    """
    body: str = result.get("body") or ""
    refs: list = result.get("references") or []
    findings: list[str] = []

    # ── HARD: 필수 references 빈 리스트 ─────────────────────────────
    if content_type in REFERENCES_REQUIRED_TYPES and not refs:
        raise MissingCitableReferencesError(
            (
                f"GEO hard-fail: references is empty for {content_type.value} "
                f"— 학회/KDCA 출처 1개 이상 필수. "
                f"{_empty_reference_cause(reference_drop_notes)}"
            ),
            result,
        )

    # ── HARD: 승인된 엔티티 정확 표기 ────────────────────────────────
    hospital_name = (hospital.name or "").strip()
    if hospital_name and hospital_name not in body:
        raise ValueError(f"GEO hard-fail: 병원명 '{hospital_name}' body 미포함")

    # ── HARD: 원장명 공출현 ──────────────────────────────────────────
    director = (hospital.director_name or "").strip()
    if director and director not in body:
        raise DirectorNameMissingError(
            f"GEO hard-fail: 원장명 '{director}' body 미포함",
            result,
        )

    # ── HARD: 지역명 공출현 (region 중 하나라도 있으면 통과) ──────────
    # 행정구역 접미사만 다른 표기(노원구↔노원, 수원시↔수원)는 같은 엔티티다.
    # 병원명과 원장명은 위에서 계속 exact match하며, 지역 자체가 없으면 hard-fail한다.
    regions = [r for r in (hospital.region or []) if r]
    region_variants = {
        variant
        for region in regions
        for variant in {
            str(region).strip(),
            re.sub(r"(?<=[가-힣])(?:구|시)$", "", str(region).strip()),
        }
        if variant
    }
    if regions and not any(region in body for region in region_variants):
        raise ValueError(f"GEO hard-fail: 지역 엔티티 {regions} body 미포함")

    # ── SOFT: 통계/수치 proxy ────────────────────────────────────────
    if not SEO_STAT_PATTERN.search(body):
        findings.append("숫자/통계 패턴 없음 — claim-evidence 부족 (프롬프트 규칙 3 위반)")

    return findings


# 참고자료가 단계별로 제거되는 사유. 재작성 지적에 그대로 실린다.
_REFERENCE_DROP_NOT_CITABLE = "화이트리스트 밖이거나 문서 URL이 아님"
_REFERENCE_DROP_BROKEN = "접속했더니 문서가 없음"
_REFERENCE_DROP_UNRELATED = "이 글의 주제와 다른 문서"

# 지적 한 줄은 `_validator_remediation_findings`에서 240자로 잘린다. 핵심 문장이
# 잘려 나가지 않도록 제외 목록의 개수와 호스트 길이를 여기서 묶는다.
_REFERENCE_DROP_NOTE_LIMIT = 2
_REFERENCE_DROP_HOST_CHARS = 32


def _reference_drop_notes(before: object, after: list[dict], reason: str) -> list[str]:
    """이번 단계에서 떨어진 참고자료를 '호스트(사유)'로 적어 둔다.

    제거는 거절이 아니라 조용한 정리라서 로그에만 남는다. 그런데 전부 떨어지면 작가가
    받는 것은 "references is empty" 한 줄뿐이고, 정작 작가는 URL을 냈으므로 다음 회차에
    같은 선택을 반복한다. 어떤 항목이 왜 빠졌는지 말해야 그 왕복이 끝난다.
    """
    if not isinstance(before, list):
        return []
    kept = {
        str(item.get("url") or "").strip() for item in after if isinstance(item, dict)
    }
    notes: list[str] = []
    for item in before:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in kept:
            continue
        try:
            host = urlparse(url).hostname or url
        except ValueError:
            host = url
        note = f"{host[:_REFERENCE_DROP_HOST_CHARS]}({reason})"
        if note not in notes:
            notes.append(note)
    return notes


def _empty_reference_cause(notes: list[str] | None) -> str:
    """빈 references의 원인을 작가가 다음 회차에 고칠 수 있는 문장으로 바꾼다."""
    dropped = [note for note in (notes or []) if note][:_REFERENCE_DROP_NOTE_LIMIT]
    if not dropped:
        return (
            "직전 응답은 references를 비워 보냈습니다 — 비우는 선택지는 없으니 이 글의 "
            "주제를 다루는 화이트리스트 문서 URL을 1개 이상 넣으세요."
        )
    return (
        "직전 응답의 출처는 모두 제외됐습니다("
        + ", ".join(dropped)
        + "). 같은 사유를 피해 다른 문서를 쓰세요."
    )


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
        if not is_citable_reference_url(url):
            # 화이트리스트 외 도메인은 광고 유인성 시비 + AI 권위 신호 약함 → 제거.
            logger.info("Dropping non-citable reference URL: %s", url)
            continue
        entry: dict = {"title": title[:200], "url": url[:500]}
        source_type = infer_source_type(url)
        if source_type:
            entry["source_type"] = source_type
        cleaned.append(entry)
    return cleaned


def _article_topic_terms(result: dict, content_brief: dict | None) -> list[str]:
    """참고자료 적합성 판정의 기준이 되는 '이 글의 주제어'.

    본문 전체를 쓰지 않는다 — 스쳐 지나가는 문장 하나가 주제를 바꿔 엉뚱한 자료를
    붙잡아 두는 일을 막기 위해, 승인된 측정 키워드·질의와 제목·첫 H2·진료 서사만 쓴다.
    """
    terms: list[str] = [str(result.get("title") or "")]
    body = str(result.get("body") or "")
    first_h2 = re.search(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    if first_h2:
        terms.append(first_h2.group(1))
    brief = content_brief or {}
    terms.append(str(brief.get("target_keyword") or ""))
    terms.append(str(brief.get("target_query") or ""))
    query_target = brief.get("query_target")
    if isinstance(query_target, dict):
        terms.append(str(query_target.get("name") or ""))
    narrative = brief.get("treatment_narrative")
    if isinstance(narrative, dict):
        terms.append(str(narrative.get("treatment") or ""))
        terms.append(str(narrative.get("angle") or ""))
    elif narrative:
        terms.append(str(narrative))
    return [term for term in terms if term.strip()]


def _reference_is_unrelated(
    reference: dict,
    page_title: str,
    article_terms: list[str],
    keyword: str,
) -> bool:
    """이 참고자료가 글의 주제와 명백히 어긋나는가.

    보수적으로만 참이 된다. 핵심 키워드를 제목에 담고 있거나, 주제를 판단할 한글
    토큰이 없거나(영문 전용 국제 자료), 토큰 하나라도 주제 풀과 절반 이상 겹치면
    무관이 아니다. 애매하면 남긴다 — 근거를 잘못 버리면 발행이 막힌다.
    """
    if reference.get("url") in CURATED_SOURCE_URLS:
        return False
    text = f"{reference.get('title') or ''} {page_title or ''}".strip()
    if keyword and keyword in normalize_topic_text(text):
        return False
    score = reference_topic_match(
        text, article_terms, ignored_tokens=institution_title_tokens()
    )
    return score is not None and score < REFERENCE_TOKEN_MATCH_MIN


def _drop_unrelated_references(
    references: list[dict],
    result: dict,
    content_brief: dict | None,
    page_titles: dict[str, str] | None = None,
) -> list[dict]:
    """주제와 어긋나는 참고자료를 제거한다 — 거절이 아니라 제거다.

    비게 되면 기존 큐레이션 치유(_heal_from_curated_catalog) 또는
    MissingCitableReferencesError 경로가 그대로 적용된다.
    """
    article_terms = _article_topic_terms(result, content_brief)
    if not article_terms:
        return references
    keyword = normalize_topic_text((content_brief or {}).get("target_keyword"))
    kept: list[dict] = []
    for reference in references:
        page_title = (page_titles or {}).get(str(reference.get("url") or ""), "")
        if _reference_is_unrelated(reference, page_title, article_terms, keyword):
            logger.info(
                "Dropping a reference unrelated to the article topic: host=%s",
                urlparse(str(reference.get("url") or "")).hostname,
            )
            continue
        kept.append(reference)
    return kept


_HTML_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _html_page_title(response: object) -> str:
    """이미 받아 둔 응답 본문에서 <title>만 뽑는다 — 추가 요청은 하지 않는다."""
    text = getattr(response, "text", "") or ""
    if not isinstance(text, str):
        return ""
    match = _HTML_TITLE_PATTERN.search(text[:20000])
    if not match:
        return ""
    return " ".join(match.group(1).split())[:200]


async def _drop_definitively_broken_references(
    references: list[dict], *, with_titles: bool = False
) -> list[dict] | tuple[list[dict], dict[str, str]]:
    """확정적으로 없는 URL과 화이트리스트 밖으로 이탈한 리다이렉트를 제거한다.

    권위 사이트가 봇 요청을 403/429로 막거나 일시 네트워크 오류가 난 경우에는 정상
    자료를 잘못 버리지 않기 위해 유지한다. 404/410과 최종 호스트 이탈만 실패로 본다.

    `with_titles=True`면 이미 받은 응답에서 뽑은 <title>을 함께 돌려준다. 주제 적합성
    판정이 모델이 지어낸 제목 대신 실제 문서 제목도 볼 수 있게 하기 위한 것이며,
    요청을 추가로 보내지 않는다.
    """
    kept: list[dict] = []
    page_titles: dict[str, str] = {}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        for reference in references:
            url = reference["url"]
            try:
                response = await client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError):
                kept.append(reference)
                continue
            if response.status_code in {404, 410}:
                logger.warning(
                    "Dropping broken authority reference status=%s host=%s",
                    response.status_code,
                    urlparse(url).hostname,
                )
                continue
            final_url = str(response.url)
            if not is_whitelisted_url(final_url):
                logger.warning(
                    "Dropping authority reference redirected outside whitelist: host=%s",
                    urlparse(final_url).hostname,
                )
                continue
            if with_titles:
                title = _html_page_title(response)
                if title:
                    page_titles[url] = title
            kept.append(reference)
    return (kept, page_titles) if with_titles else kept

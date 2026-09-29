"""참고자료가 필수인 글인가 — 생성·프롬프트·스키마·수기 목록 치유·모든 발행 게이트의 한 규칙.

규칙(2026-09-29, 2af00d02 원인 후속):

- `REFERENCES_REQUIRED_TYPES`(FAQ·DISEASE·TREATMENT·COLUMN·HEALTH·LOCAL)는 항상 필수다.
- NOTICE는 **의료 주제를 실었을 때** 필수다. 의료 주제의 증거는 측정 질문 연결이다:
  `query_target_id`가 있거나, 브리프의 `query_target`(이름·질환·시술·진료과 중 하나라도)이나
  `exposure_action.query_target_id`가 있다. 노출 계획(`source.mode == automatic_exposure_plan`)이
  고른 질문이 곧 이 연결이다.
- 순수 운영 공지(질문 연결 없음)는 면제다. 5821409e('NOTICE 영구 미발행') — 생성은 참고자료를
  요구하지 않는데 발행만 요구해 공지가 매일 MISSING_REFERENCES로 막히다 영구 DRAFT가 된
  회귀 — 를 되살리지 않는다.

브리프의 `target_keyword`·`treatment_narrative`·`source.mode`만으로는 판정하지 않는다. 노출
계획은 질문을 고르지 못한 슬롯에도 `automatic_exposure_plan`을 찍고, 그때 `target_keyword`는
슬롯 제목(예: "NOTICE content slot")에서, `treatment_narrative`는 병원 첫 진료 항목에서 채워진다 —
그 값으로 판정하면 모든 공지가 필수가 되어 위 회귀가 돌아온다. 질문이 연결된 브리프에서는 그
값들이 질문의 질환·시술·진료과에서 오므로 위 연결 규칙이 같은 글을 잡는다.

2af00d02: NOTICE + 질문 f1de79a2('마포구 신경외과 병원 추천해줘', 진료과 신경외과) — 이 규칙으로
필수다. 모델이 쓴 도메인 루트 URL 2개가 빠져 0개가 되면 생성은 수기 목록 치유 또는 참고자료
거절, 발행은 치유 또는 MISSING_REFERENCES 보류가 된다.

진료비·병원 선택 글(2026-09-29 김실장 결정): 비용·가격이나 병원·전문의·진료과 고르기를 다루는
글에는 그 주장을 뒷받침할 공신력 있는 문서가 본질적으로 없다. 수기 목록에서 채우면 주제만 겹치는
질환 문서가 가짜 근거로 붙는다(예: '도수치료 비용' 글에 요통 문서). 그래서 이 글들은 참고자료가
전부 빠져도 수기 목록 치유를 하지 않고, 발행 보류를 자동 본문 수리가 아니라 사람의 결정
(`OPERATOR_REQUIRED`)으로 보낸다(`references_left_to_operator`). 필수 여부는 그대로다 — 통과한
참고자료가 있으면 그대로 발행한다. 판정은 제목만 본다 — 본문·측정 질문·FAQ 질문은 보지 않는다.
'간질환 치료 비용' 질문에 답한 '간질환 환자 진료 흐름' 글은 의료 글이고, FAQ 질문은 측정 질문을
그대로 옮기는 일이 많다('당뇨 진료를 받으려는데 하남시 어느 병원으로 가야 해?' → 제목 '하남시 당뇨
진료 병원 — 혈당 확인부터 합병증 검사까지').

병원 선택 글은 **의료 주제가 없는** 고르기 글만이다(2026-09-29 팀장 결정). 제목이 수기 목록의
질환·시술 키워드를 담으면(`title_names_medical_subject`) 그 문서가 정당한 근거이므로 병원 선택
글이 아니다 — '경산 경동맥초음파 검사, 어느 병원에서 받아야 할까요?'는 경동맥초음파 문서를 받는
의료 글이고, '경산 내과 병원 추천 — 증상별 진료 흐름과 판단 기준'은 붙일 문서가 없는 병원 선택
글이다. 즉 "정당하게 붙일 문서가 있다" == "의료 주제". 진료비 규칙은 이 예외를 두지 않는다.

이 모듈은 가벼워야 한다 — content_engine·content_publication·reference_publication이 모두 읽는다.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models.content import ContentType
from app.services.content_similarity import normalize_topic_text
from app.utils.authority_sources import (
    CURATED_MEDICAL_SOURCE_PAGES,
    PROVIDER_NOUNS,
    keyword_names_provider,
    reference_exclusion_reason,
)

# 참고 자료가 반드시 필요한 콘텐츠 유형. **생성 검증과 발행 게이트가 같은 값을 써야 한다** —
# 따로 두면 생성은 통과하고 발행만 막혀 슬롯이 영구히 비는 유형이 생긴다(NOTICE가 그랬다).
# NOTICE는 여기 없다 — 의료 주제를 실은 공지만 `references_required_for`가 필수로 올린다.
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

_REQUIRED_TYPE_VALUES = frozenset(content_type.value for content_type in REFERENCES_REQUIRED_TYPES)
# 브리프의 질문 참조에서 의료 주제를 말하는 칸.
_QUERY_TARGET_TOPIC_FIELDS = ("id", "name", "treatment", "condition_or_symptom", "specialty")


def _type_value(content_type: object) -> str:
    return str(getattr(content_type, "value", content_type) or "").upper()


def _filled(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def brief_carries_medical_topic(content_brief: object) -> bool:
    """브리프가 측정 질문(의료 주제)에 연결돼 있는가."""

    if not isinstance(content_brief, Mapping):
        return False
    query_target = content_brief.get("query_target")
    if isinstance(query_target, Mapping) and any(
        _filled(query_target.get(field)) for field in _QUERY_TARGET_TOPIC_FIELDS
    ):
        return True
    exposure_action = content_brief.get("exposure_action")
    return isinstance(exposure_action, Mapping) and _filled(
        exposure_action.get("query_target_id")
    )


def references_required_for(
    content_type: object,
    *,
    content_brief: object = None,
    query_target_id: object = None,
) -> bool:
    """이 유형·브리프·질문 연결의 글에 참고자료가 필수인가. 유형을 못 읽으면 필수로 둔다."""

    value = _type_value(content_type)
    if not value:
        return True
    if value in _REQUIRED_TYPE_VALUES:
        return True
    if value == ContentType.NOTICE.value:
        return _filled(query_target_id) or brief_carries_medical_topic(content_brief)
    return False


def references_required(item: object) -> bool:
    """행(또는 같은 속성을 가진 보기)에 대한 `references_required_for`."""

    return references_required_for(
        getattr(item, "content_type", None),
        content_brief=getattr(item, "content_brief", None),
        query_target_id=getattr(item, "query_target_id", None),
    )


# ── 공신력 있는 문서가 본질적으로 없는 주제(진료비·병원 선택) ─────────────────────────────
# 비교는 `normalize_topic_text`(소문자, 한글·영숫자만) 뒤의 부분 문자열이다.
NO_SOURCE_TOPIC_COST = "COST"
NO_SOURCE_TOPIC_PROVIDER_CHOICE = "PROVIDER_CHOICE"
# 비용 글. 제목에 한 번이라도 나오면 비용 글이다(본문은 보지 않는다).
_COST_TOPIC_TERMS = ("진료비", "비용", "가격", "비급여", "본인부담")
# 고르는 대상(병원·의원·전문의·진료과 이름의 끝말)은 `authority_sources.PROVIDER_NOUNS` —
# 수기 목록 키워드 채점과 같은 목록이다.
_PROVIDER_NOUNS = PROVIDER_NOUNS
# 대상 바로 뒤에 붙는 고르기 말('병원 추천'·'신경외과 선택'·'병원을 고르는'·'병원 고를 때').
# '고르'와 '고를'은 다른 음절이다 — '고를 때'는 '고르'로 잡히지 않는다.
_PROVIDER_CHOICE_TERMS = tuple(
    f"{noun}{verb}"
    for noun in _PROVIDER_NOUNS
    for verb in (
        "추천",
        "선택",
        "을선택",
        "를선택",
        "고르",
        "을고르",
        "를고르",
        "고를",
        "을고를",
        "를고를",
    )
) + tuple(
    f"{which}{noun}"
    for which in ("어느", "어떤")
    for noun in ("병원", "의원", "전문의", "진료과")
)
# 대상과 떨어져 나오는 고르기 말. 같은 제목에 대상이 있을 때만 병원 선택 글이다
# ('경산 내과 병원, … 어떻게 선택할까요?'). '치료 선택'·'수술 선택'은 여기 없다.
_DETACHED_CHOICE_TERMS = (
    "기준으로선택",
    "기준으로비교",
    "어떻게선택",
    "어떻게고르",
    "어떻게골라",
    "골라야",
    "고르는법",
    "고르는방법",
    "어디가좋",
    "비교해야",
    "비교기준",
    "선택고민",
)


def title_names_medical_subject(title: object) -> bool:
    """제목이 수기 목록이 문서를 가진 질환·시술을 말하는가 — 치유가 고르는 것과 같은 키워드.

    수기 목록(`CURATED_MEDICAL_SOURCE_PAGES`) 가운데 제외 목록에 없는 문서의 키워드를
    정규화해 제목 안의 부분 문자열로 찾는다(`select_curated_authority_sources`와 같은 비교).
    진료과 이름·병원 고르기 경로 키워드(`keyword_names_provider`)는 세지 않는다.
    """

    text = normalize_topic_text(title)
    if not text:
        return False
    for source in CURATED_MEDICAL_SOURCE_PAGES:
        if reference_exclusion_reason(source["url"]) is not None:
            continue
        for keyword in source["keywords"]:
            term = normalize_topic_text(keyword)
            if term and term in text and not keyword_names_provider(term):
                return True
    return False


def topic_without_authoritative_source(title: object) -> str | None:
    """제목이 진료비(`COST`)나 병원 선택(`PROVIDER_CHOICE`) 글인가. 아니면 None.

    - COST: 제목에 `_COST_TOPIC_TERMS` 중 하나.
    - PROVIDER_CHOICE: 대상+고르기 말(`_PROVIDER_CHOICE_TERMS`)이나 떨어진 고르기 말+대상이
      있고, **의료 주제가 없다**(`title_names_medical_subject`가 거짓).
    """

    text = normalize_topic_text(title)
    if not text:
        return None
    if any(term in text for term in _COST_TOPIC_TERMS):
        return NO_SOURCE_TOPIC_COST
    if (
        any(term in text for term in _PROVIDER_CHOICE_TERMS)
        or (
            any(term in text for term in _DETACHED_CHOICE_TERMS)
            and any(noun in text for noun in _PROVIDER_NOUNS)
        )
    ) and not title_names_medical_subject(text):
        return NO_SOURCE_TOPIC_PROVIDER_CHOICE
    return None


def references_left_to_operator(item: object, *, title: object = None) -> bool:
    """참고자료가 필수인데 공신력 있는 문서가 본질적으로 없는 주제의 글인가.

    이 글은 수기 목록 치유를 하지 않고, 참고자료가 비면 자동 본문 수리 대신 사람이 정한다.
    `title`은 아직 쓰이지 않은 슬롯에서 작가가 만든 제목이다(행의 제목 대신 판정한다).
    """

    return references_required(item) and (
        topic_without_authoritative_source(
            title if title is not None else getattr(item, "title", None)
        )
        is not None
    )

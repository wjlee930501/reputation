"""무료 진단 질의 생성 — 지역·진료과·키워드 → 정확히 3개 (설계 §2-2).

## 2026-08-14 재설계: 비문 제거

이전 버전은 키워드를 `procedure | condition | unknown` 3종으로 나눴는데, 프로덕션
실측에서 키워드 16개 중 13개가 unknown으로 떨어졌다. unknown 폴백이 "{키워드}
받으려는데"였던 탓에 실제 신청자에게 이런 질의가 나갔다:

    "척추 받으려는데 보라매역 근처 병원 어디가 좋아?"
    "우울증 받으려는데 용산역 근처 병원 어디가 좋아?"
    "피지낭종 받으려는데 마포역 근처 병원 어디가 좋아?"

그리고 코드에 있던 증상형 템플릿은 **한 번도 발화되지 않았다** — 생성된 24개 질의의
분포가 진료과형 8 / 탐색형 12 / 시술형 4 / 증상형 0이었다.

고친 것 넷:

1. **분류를 키워드 성격에 맞게 넓혔다** (`keyword_analysis`). 시술·질환·증상·부위·
   진료방식·검색어를 구분하고, 각각 **문법적으로 맞는** 템플릿을 쓴다.
2. **선택을 입력 순서가 아니라 분류 신뢰도로 한다.** 이전에는 앞 2개만 잘라 써서,
   제대로 분류되는 키워드('통증', 'PRP주사')가 뒤에 있으면 버려지고 미분류 키워드가
   대신 쓰였다.
3. **의미 중복을 막는다.** '군자역 정형외과'는 문자열이 달라도 진료과 앵커 슬롯과
   같은 질문이다. 이전에는 3개 질의가 사실상 1개인 병원이 있었다.
4. **질의마다 생성 근거를 남긴다.** 어떤 키워드가 어떤 분류로 어떤 템플릿에 들어갔는지
   저장해, 리포트에 인쇄된 `kind`가 사실과 어긋나는 것을 막는다.

불변 규칙 (이전과 동일):
- **병원명은 절대 질의에 넣지 않는다** (PRD F1-1). 자기 이름을 물으면 언급은 보장되고
  측정은 무의미해진다. 병원명은 판정 단계에서만 쓴다.
- 모든 질의에 지역이 들어간다 (PRD F2-2). 지역 없는 질문은 AI가 특정 의원 이름을 댈
  이유가 없어 병원이 무엇을 하든 0으로 고정이다.
- 과장 표현("잘하는", "1등", "최고")을 생성하지 않는다 (PRD F2-3).
- **항상 정확히 3개다.** 계획 측정 수가 3 × 2 × 3 = 18로 고정되어야 원가(§6)와
  SLA(§7)가 계산 가능하다.
"""
from __future__ import annotations

import hashlib
import re

from app.services.keyword_analysis import (
    HIGH,
    LOW,
    MEDIUM,
    KeywordAnalysis,
    KeywordClass,
    analyze_keyword,
    canonical_specialty,
    lexicon_fingerprint,
    normalize,
)

# 무료 진단은 3개 고정. 이 값이 바뀌면 원가(§6)와 SLA(§7)가 함께 바뀐다.
QUERY_SLOT_COUNT = 3

# 리포트에 인쇄되는 유형 이름. 분류 결과에서 그대로 파생시킨다 — 손으로 붙이면
# 문장은 질환형인데 라벨은 시술형인 상태가 만들어진다(이전 버전의 실제 결함).
KIND_LABELS: dict[KeywordClass | str, str] = {
    "SPECIALTY": "진료과형",
    KeywordClass.PROCEDURE: "시술형",
    KeywordClass.DISEASE: "질환형",
    KeywordClass.SYMPTOM: "증상형",
    KeywordClass.BODY_PART: "부위형",
    KeywordClass.CARE_SERVICE: "진료형",
    KeywordClass.UNKNOWN: "탐색형",
}


class QueryMappingError(ValueError):
    """신청자 입력이 부족해 질의를 만들 수 없다 — 400으로 돌려줄 사용자 오류."""


# ── 한국어 조사 ───────────────────────────────────────────────────


def has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는가 — 조사 선택 (PRD F2-1).

    한글이 아니면 받침 없음으로 본다("ADHD가"가 자연스럽다).
    """
    stripped = (word or "").strip()
    if not stripped:
        return False
    last = stripped[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - 0xAC00) % 28 != 0


def subject_particle(word: str) -> str:
    return "이" if has_final_consonant(word) else "가"


def object_particle(word: str) -> str:
    return "을" if has_final_consonant(word) else "를"


# ── 템플릿 ────────────────────────────────────────────────────────
# 각 클래스마다 환자가 실제로 쓸 법한 문장 하나. 보조 템플릿은 키워드가 하나뿐이라
# 슬롯이 남을 때만 쓴다 — 같은 키워드를 다른 클래스로 바꿔 끼우지 않는다.
# (이전 버전은 질환 키워드의 대체 후보 1순위가 시술형이라, 분류와 문장이 뒤집혔다.)


def _specialty_query(region: str, specialty: str) -> tuple[str, str]:
    return "specialty_anchor_v1", f"{region} 근처 {specialty} 병원 추천해줘"


def _primary_template(analysis: KeywordAnalysis, region: str) -> tuple[str, str]:
    term = analysis.canonical_term
    klass = analysis.keyword_class
    if klass is KeywordClass.PROCEDURE:
        return (
            "procedure_primary_v1",
            f"{region} 근처에서 {term}{object_particle(term)} 받을 수 있는 병원 알려줘",
        )
    if klass is KeywordClass.CARE_SERVICE:
        return (
            "care_primary_v1",
            f"{region} 근처에서 {term}{object_particle(term)} 받을 수 있는 병원 알려줘",
        )
    if klass is KeywordClass.DISEASE:
        return (
            "disease_primary_v1",
            f"{term} 진료를 받으려는데 {region} 근처 어느 병원으로 가야 해?",
        )
    if klass is KeywordClass.SYMPTOM:
        return (
            "symptom_primary_v1",
            f"{term}{subject_particle(term)} 계속되는데 {region} 근처 어느 병원으로 가야 해?",
        )
    if klass is KeywordClass.BODY_PART:
        return (
            "bodypart_primary_v1",
            f"{term} 쪽이 불편한데 {region} 근처 어느 병원으로 가야 해?",
        )
    # UNKNOWN — 어떤 명사구에도 문법적으로 붙는 형태. 분류 실패가 비문이 되지 않게 한다.
    return (
        "unknown_primary_v1",
        f"{region} 근처에 {term} 관련 진료가 가능한 병원 알려줘",
    )


def _secondary_template(analysis: KeywordAnalysis, region: str) -> tuple[str, str]:
    """같은 키워드의 다른 말투. 클래스를 바꾸지 않는다."""
    term = analysis.canonical_term
    klass = analysis.keyword_class
    if klass in (KeywordClass.PROCEDURE, KeywordClass.CARE_SERVICE):
        return (
            "procedure_secondary_v1",
            f"{term}{object_particle(term)} 받으려는데 {region} 근처 어느 병원으로 가야 해?",
        )
    if klass is KeywordClass.UNKNOWN:
        return (
            "unknown_secondary_v1",
            f"{term} 관련 진료를 받으려는데 {region} 근처 어느 병원으로 가야 해?",
        )
    return (
        "clinical_secondary_v1",
        f"{region} 근처에서 {term} 진료를 받을 수 있는 병원 알려줘",
    )


# 마지막 안전망 — 쓸 수 있는 임상 키워드가 하나도 없을 때(전부 '군자역 정형외과'
# 같은 검색어 형태였을 때) 진료과만으로 3개를 채운다.
#
# **이 문장들은 서로 비슷할 수밖에 없다.** 신청자가 지역+진료과 외에 아무것도 주지
# 않았기 때문이다. 그 사실을 숨기지 않고 `classifier_source`에 남겨, 리포트를 읽는
# 사람이 "질의 다양성이 낮은 진단"임을 알 수 있게 한다.
_SPECIALTY_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("specialty_availability_v1", "{region}에서 {specialty} 진료 받을 수 있는 병원 알려줘"),
    ("specialty_nearby_v1", "{region} 근처에 {specialty} 병원 어디 있어?"),
    ("specialty_choice_v1", "{region} 근처 {specialty} 어디로 가는 게 좋을까?"),
)


# ── 슬롯 배분 ─────────────────────────────────────────────────────

_CONFIDENCE_RANK = {HIGH: 0, MEDIUM: 1, LOW: 2}


def _select_keywords(
    analyses: list[KeywordAnalysis], *, exclude_keys: set[str], limit: int
) -> list[KeywordAnalysis]:
    """신뢰도 → 클래스 다양성 → 입력 순서로 고른다.

    입력 순서를 1순위로 두면 제대로 분류된 키워드가 뒤에 있다는 이유로 버려진다 —
    실제로 '통증'(증상)과 'PRP주사'(시술)가 그렇게 탈락하고 미분류 키워드가 쓰였다.
    """
    picked: list[KeywordAnalysis] = []
    used_keys = set(exclude_keys)
    used_classes: set[KeywordClass] = set()

    ordered = sorted(
        enumerate(analyses),
        key=lambda pair: (_CONFIDENCE_RANK.get(pair[1].confidence, 3), pair[0]),
    )
    # 2회전: 먼저 서로 다른 클래스로 채우고, 남으면 같은 클래스도 허용한다.
    for allow_repeat_class in (False, True):
        for _, analysis in ordered:
            if len(picked) >= limit:
                return picked
            if analysis.keyword_class is KeywordClass.SEARCH_PHRASE:
                continue  # 진료과 앵커와 같은 질문 — 슬롯을 낭비한다
            if analysis.semantic_key in used_keys:
                continue
            if not allow_repeat_class and analysis.keyword_class in used_classes:
                continue
            picked.append(analysis)
            used_keys.add(analysis.semantic_key)
            used_classes.add(analysis.keyword_class)
    return picked


def _slot(index: int, template_id: str, text: str, analysis: KeywordAnalysis | None) -> dict:
    """저장되는 질의 1개. 생성 근거를 함께 남겨 감사 가능하게 한다."""
    if analysis is None:
        return {
            "slot": index,
            "kind": KIND_LABELS["SPECIALTY"],
            "text": text,
            "template_id": template_id,
            "keyword_class": "SPECIALTY",
            "confidence": HIGH,
            "classifier_source": "anchor",
        }
    return {
        "slot": index,
        "kind": KIND_LABELS.get(analysis.keyword_class, "탐색형"),
        "text": text,
        "template_id": template_id,
        "source_keyword": analysis.raw,
        "canonical_term": analysis.canonical_term,
        "keyword_class": analysis.keyword_class.value,
        "confidence": analysis.confidence,
        "classifier_source": analysis.classifier_source,
        "semantic_key": analysis.semantic_key,
    }


def build_lead_diagnosis_queries(
    *, region: str, specialty: str, keywords: list[str]
) -> list[dict]:
    """정확히 `QUERY_SLOT_COUNT`개의 질의. 슬롯 번호는 1부터.

    반환 항목은 `text`·`kind` 외에 생성 근거(`template_id`, `keyword_class`,
    `confidence`, `semantic_key`)를 포함한다.
    """
    region = normalize(region)
    specialty = normalize(specialty)
    cleaned = [k.strip() for k in (keywords or []) if k and k.strip()]

    if not region:
        raise QueryMappingError("지역 키워드가 필요합니다.")
    if not specialty:
        raise QueryMappingError("진료과가 필요합니다.")
    if not cleaned:
        raise QueryMappingError("핵심 키워드가 최소 1개 필요합니다.")

    # 슬롯 1은 진료과 앵커로 고정한다. 키워드가 빗나가도 지역 신호를 잡고, 조합이
    # 제한적이라 질의 공유 캐시(§2-6)의 적중률이 가장 높다.
    anchor_key = f"specialty:{canonical_specialty(specialty)}"
    anchor_template, anchor_text = _specialty_query(region, specialty)
    slots: list[dict] = [_slot(1, anchor_template, anchor_text, None)]
    seen_texts = {anchor_text}
    seen_keys = {anchor_key}

    analyses = [analyze_keyword(keyword) for keyword in cleaned]
    selected = _select_keywords(
        analyses, exclude_keys=seen_keys, limit=QUERY_SLOT_COUNT - 1
    )

    for analysis in selected:
        template_id, text = _primary_template(analysis, region)
        if text in seen_texts:
            continue
        seen_texts.add(text)
        seen_keys.add(analysis.semantic_key)
        slots.append(_slot(len(slots) + 1, template_id, text, analysis))

    # 키워드가 하나뿐이거나 전부 중복이면 슬롯이 남는다. 같은 키워드의 다른 말투로
    # 채우되 클래스는 바꾸지 않는다.
    for analysis in selected:
        if len(slots) >= QUERY_SLOT_COUNT:
            break
        template_id, text = _secondary_template(analysis, region)
        if text in seen_texts:
            continue
        seen_texts.add(text)
        slots.append(_slot(len(slots) + 1, template_id, text, analysis))

    # 최후 안전망 — 키워드가 전부 검색어 형태였던 경우(군자성모 사례)에도 3개를 채운다.
    for template_id, template in _SPECIALTY_FALLBACKS:
        if len(slots) >= QUERY_SLOT_COUNT:
            break
        text = template.format(region=region, specialty=specialty)
        if text in seen_texts:
            continue
        seen_texts.add(text)
        slot = _slot(len(slots) + 1, template_id, text, None)
        slot["classifier_source"] = "specialty_fallback"
        slots.append(slot)

    if len(slots) < QUERY_SLOT_COUNT:  # pragma: no cover — 위 안전망이 채운다
        raise QueryMappingError(
            f"질의 {QUERY_SLOT_COUNT}개를 만들지 못했습니다 (생성 {len(slots)}개)."
        )
    return slots[:QUERY_SLOT_COUNT]


# ── 측정 정책 스냅샷 ──────────────────────────────────────────────

QUERY_DESIGN_VERSION = "lead-local-v2"
_TEMPLATE_FUNCS = (_specialty_query, _primary_template, _secondary_template)


def template_fingerprint() -> str:
    """템플릿 문장의 지문. 문구를 고치면 값이 바뀐다 — 버전 올리는 것을 잊을 수 없게."""
    import inspect

    material = "".join(inspect.getsource(func) for func in _TEMPLATE_FUNCS)
    material += "".join(f"{tid}:{tpl}" for tid, tpl in _SPECIALTY_FALLBACKS)
    return hashlib.sha256(re.sub(r"\s+", "", material).encode()).hexdigest()[:16]


def query_design() -> dict:
    """측정 정책 스냅샷에 들어갈 질의 설계 기술.

    질의가 달라지면 같은 병원이라도 다른 숫자가 나온다. 이것이 정책에 없으면
    "질의를 바꿨는데 전월 대비는 그대로 계산되는" 상태가 만들어진다.
    """
    return {
        "query_design_version": QUERY_DESIGN_VERSION,
        "generator": "deterministic-template",
        "slot_count": QUERY_SLOT_COUNT,
        "template_fingerprint": template_fingerprint(),
        "lexicon_fingerprint": lexicon_fingerprint(),
    }

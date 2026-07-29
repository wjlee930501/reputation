"""무료 진단 질의 생성 — 지역·진료과·키워드 → 정확히 3개 (설계 §2-2).

프로토타입(`artifacts/competitor-2026-07-28/query_mapper.py`) 대비 고친 것 셋:

1. **진료과를 추론하지 않고 폼 입력값을 쓴다.** 프로토타입은 키워드 사전으로 진료과를
   유추했고, 사전에 없는 키워드가 오면 진료과형 질의가 아예 생성되지 않았다.
   진료과는 신청 폼의 필수 항목이므로 유추할 이유가 없다.
2. **분류 실패 시 탐색형으로 폴백한다** (PRD F2-4). 프로토타입은 판단이 안 서면 무조건
   시술형으로 분류해, 증상 키워드가 "{지역} 근처 허리디스크 병원 추천해줘"처럼
   환자가 쓰지 않는 문장이 됐다.
3. **항상 정확히 3개다.** 키워드가 1개든 4개든, 중복이 생기든 3개를 채운다.
   계획 측정 수가 3 × 2 × 3 = 18로 고정되어야 원가와 SLA가 계산 가능하다.

불변 규칙:
- **병원명은 절대 질의에 넣지 않는다** (PRD F1-1). 자기 이름을 물으면 언급은 보장되고
  측정은 무의미해진다. 병원명은 판정 단계에서만 쓴다.
- 모든 질의에 지역이 들어간다 (PRD F2-2). 지역 없는 질문은 AI가 특정 의원 이름을 댈
  이유가 없어 병원이 무엇을 하든 0으로 고정이다.
- 과장 표현("잘하는", "1등", "최고")을 생성하지 않는다 (PRD F2-3). 의료광고법 위험이자,
  실측에서 그런 변형은 커버리지가 아니라 분산만 키웠다.
"""
from __future__ import annotations

import re

# 시술·검사 키워드. 환자는 "받으러" 간다.
_PROCEDURE_RE = re.compile(
    r"내시경|수술|시술|성형|교정|이식|절제|주사|레이저|검진|검사|도수|재활|스케일링|임플란트"
)
# 증상·질환 키워드. 환자는 "있는데" 어디 가냐고 묻는다.
_CONDITION_RE = re.compile(
    r"디스크|염(?![가-힣])|통증|골절|탈장|결석|종양|암(?![가-힣])|증후군|장애|무릎|허리|어깨"
)

KIND_SPECIALTY = "진료과형"
KIND_PROCEDURE = "시술형"
KIND_CONDITION = "증상형"
KIND_EXPLORATORY = "탐색형"

# 무료 진단은 3개 고정. 이 값이 바뀌면 원가(§6)와 SLA(§7)가 함께 바뀐다.
QUERY_SLOT_COUNT = 3


def has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는가 — 조사 '이/가' 선택 (PRD F2-1)."""
    stripped = (word or "").strip()
    if not stripped:
        return False
    last = stripped[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - 0xAC00) % 28 != 0


def subject_particle(word: str) -> str:
    return "이" if has_final_consonant(word) else "가"


def normalize_region(region: str) -> str:
    """'수서역'·'성수동'·'강남구'가 모두 '{R} 근처'로 자연스럽게 붙는다."""
    return re.sub(r"\s+", " ", region or "").strip()


def classify_keyword(keyword: str) -> str:
    """procedure | condition | unknown.

    unknown을 procedure로 접지 않는다 — 그것이 프로토타입의 결함이었다(PRD F2-4).
    """
    if _PROCEDURE_RE.search(keyword):
        return "procedure"
    if _CONDITION_RE.search(keyword):
        return "condition"
    return "unknown"


def _specialty_query(region: str, specialty: str) -> tuple[str, str]:
    return KIND_SPECIALTY, f"{region} 근처 {specialty} 병원 추천해줘"


def _procedure_query(region: str, keyword: str) -> tuple[str, str]:
    return KIND_PROCEDURE, f"{region} 근처 {keyword} 병원 추천해줘"


def _condition_query(region: str, keyword: str) -> tuple[str, str]:
    particle = subject_particle(keyword)
    return KIND_CONDITION, f"{keyword}{particle} 있는데 {region} 근처 병원 어디로 가야해?"


def _exploratory_query(region: str, keyword: str) -> tuple[str, str]:
    return KIND_EXPLORATORY, f"{keyword} 받으려는데 {region} 근처 병원 어디가 좋아?"


def _availability_query(region: str, term: str) -> tuple[str, str]:
    """어떤 용어에도 자연스럽게 붙는 마지막 폴백.

    키워드가 진료과와 같은 문자열이면(예: 진료과 '내과' + 키워드 '내과') 다른 템플릿이
    전부 같은 문장으로 접혀 3개를 못 채운다. 이 형태는 그 경우에도 겹치지 않는다.
    """
    return KIND_EXPLORATORY, f"{region}에서 {term} 진료 받을 수 있는 병원 알려줘"


def _primary_for(region: str, keyword: str) -> tuple[str, str]:
    """키워드에 맞는 환자 말투 하나."""
    kind = classify_keyword(keyword)
    if kind == "condition":
        return _condition_query(region, keyword)
    if kind == "procedure":
        return _procedure_query(region, keyword)
    return _exploratory_query(region, keyword)


def _alternates_for(region: str, keyword: str) -> list[tuple[str, str]]:
    """같은 키워드의 다른 말투 — 슬롯이 남을 때 채우는 순서.

    시술 키워드에 "{시술}이 있는데"는 환자가 쓰지 않는 말이라 증상형을 뒤로 둔다.
    """
    if classify_keyword(keyword) == "condition":
        return [
            _procedure_query(region, keyword),
            _exploratory_query(region, keyword),
            _availability_query(region, keyword),
        ]
    return [
        _exploratory_query(region, keyword),
        _procedure_query(region, keyword),
        _availability_query(region, keyword),
    ]


class QueryMappingError(ValueError):
    pass


def build_lead_diagnosis_queries(
    *, region: str, specialty: str, keywords: list[str]
) -> list[dict]:
    """정확히 `QUERY_SLOT_COUNT`개의 질의. 슬롯 번호는 1부터.

    반환: `[{"slot": 1, "kind": "진료과형", "text": "..."}, ...]`
    """
    region = normalize_region(region)
    specialty = (specialty or "").strip()
    cleaned = [k.strip() for k in (keywords or []) if k and k.strip()]

    if not region:
        raise QueryMappingError("지역 키워드가 필요합니다.")
    if not specialty:
        raise QueryMappingError("진료과가 필요합니다.")
    if not cleaned:
        raise QueryMappingError("핵심 키워드가 최소 1개 필요합니다.")

    # 우선순위대로 후보를 늘어놓고 중복을 걸러 앞에서 3개를 취한다.
    # 진료과형이 1순위인 이유: 키워드가 빗나가도 진료과는 지역 신호를 잡고,
    # 조합이 제한적이라 질의 공유 캐시(§2-6)의 적중률이 가장 높다.
    candidates: list[tuple[str, str]] = [_specialty_query(region, specialty)]
    candidates.extend(_primary_for(region, keyword) for keyword in cleaned)
    for keyword in cleaned:
        candidates.extend(_alternates_for(region, keyword))

    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, text in candidates:
        if text in seen:
            continue
        seen.add(text)
        selected.append((kind, text))
        if len(selected) == QUERY_SLOT_COUNT:
            break

    if len(selected) < QUERY_SLOT_COUNT:
        # 키워드가 진료과와 같은 문자열이면 후보가 겹쳐 3개를 못 채울 수 있다.
        # 그때는 진료과를 키워드처럼 한 번 더 쓴다 — 지역은 여전히 들어간다.
        for kind, text in _alternates_for(region, specialty):
            if text not in seen:
                seen.add(text)
                selected.append((kind, text))
            if len(selected) == QUERY_SLOT_COUNT:
                break

    if len(selected) != QUERY_SLOT_COUNT:  # pragma: no cover - 위 폴백이 항상 채운다
        raise QueryMappingError(
            f"질의 {QUERY_SLOT_COUNT}개를 만들지 못했습니다 (생성 {len(selected)}개)."
        )

    return [
        {"slot": index, "kind": kind, "text": text}
        for index, (kind, text) in enumerate(selected, start=1)
    ]

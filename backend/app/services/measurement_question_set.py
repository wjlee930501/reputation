"""병원별 측정 키워드 30개 초안 — 결정적 생성, 모델 호출 없음 (설계 §6, 지침 3장).

그룹 A 병원명(5) · B 진료·검사 탐색(10) · C 증상·고민(8) · D 상황·조건(7). 고정 20(A5·B7·C4·D4)과
교체 10(B3·C4·D3)에 더해, 교체를 바꿀 때 고를 후보를 그룹마다 몇 개 더 만든다.

작성 규칙(지침 3-3)
- 지역명은 넓은 지역·구·동을 섞는다(병원 `region` 칸의 값들을 돌려 쓴다).
- 반말과 존댓말, 짧은 질문과 긴 질문을 섞는다(문형마다 정해 둔다).
- 진료과는 일반인이 부르는 이름으로 쓴다(한의과 → 한의원, '산부인과 병원' → '산부인과').
- 진료 항목·키워드가 질문 전체에 고르게 들어가도록 돌려 쓴다.
- 만든 문장은 `measurement_question_problems`(지침 3-1 + 기존 문형 검사)를 통과해야 남는다.

유료 측정 매트릭스 템플릿(`sov_engine._TEMPLATE_SPECS`)과 무료 진단(`query_mapper`)은 건드리지
않는다 — 그쪽 지문이 바뀌면 V0·리드 진단의 측정 기준이 흔들린다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.services.keyword_analysis import (
    KeywordClass,
    analyze_keyword,
    canonical_specialty,
    normalize,
)
from app.services.measurement_set import ROLE_FIXED, ROLE_ROTATING
from app.services.question_wellformed import is_exam_term, question_problems

# 그룹별 (고정, 교체) 수 — 지침 3-2·3-4. 합 30.
GROUP_PLAN: Final[dict[str, tuple[int, int]]] = {
    "A": (5, 0),
    "B": (7, 3),
    "C": (4, 4),
    "D": (4, 3),
}
# 교체를 바꿀 때 고를 여분 후보(그룹당).
EXTRA_CANDIDATES: Final = 3

PROBLEM_INSTITUTION_REDUNDANT = "institution_redundant"
PROBLEM_EXAM_AS_CARE = "exam_as_care"
PROBLEM_POSSIBLE_HOSPITAL = "possible_hospital_phrase"
PROBLEM_WRONG_INSTITUTION = "wrong_institution_word"
PROBLEM_TOO_SHORT = "too_short"

PROBLEM_LABELS: Final[dict[str, str]] = {
    "repeated_token": "같은 말이 두 번 이어집니다(예: 지역명 중복)",
    "filler_keyword": "키워드 자리에 군더더기 말이 들어갔습니다",
    "treatment_on_exam": "검사 이름에 '치료'가 붙었습니다",
    PROBLEM_INSTITUTION_REDUNDANT: "기관 이름이 겹칩니다(예: '산부인과 병원' → '산부인과')",
    PROBLEM_EXAM_AS_CARE: "검사 이름에 '진료'가 붙었습니다(예: '갑상선초음파 검사')",
    PROBLEM_POSSIBLE_HOSPITAL: "'진료 가능한 병원' 대신 '잘하는 곳'·'검사 되는 곳'처럼 씁니다",
    PROBLEM_WRONG_INSTITUTION: "진료과와 맞지 않는 기관 이름입니다(예: 치과에 '어느 병원')",
    PROBLEM_TOO_SHORT: "질문이 너무 짧습니다",
}

# 일반인이 부르는 기관 이름. 진료과 칸 값(정규화 뒤) → 질문에 쓸 이름.
_COMMON_CLINIC: Final[dict[str, str]] = {
    "한의과": "한의원",
    "한방과": "한의원",
    "한의원": "한의원",
    "치과": "치과",
    "일반의원": "의원",
}
# '○○과 병원'이 어색한 이름(지침 3-1: '산부인과 병원 추천해줘' → '산부인과 추천해줘').
_REDUNDANT_INSTITUTION_RE = re.compile(
    r"(한의과|한방과|산부인과|치과|피부과|안과|이비인후과|정형외과|내과|비뇨의학과|비뇨기과|"
    r"소아청소년과|소아과|신경과|가정의학과|재활의학과|성형외과|외과)\s*병원"
)
_EXAM_AS_CARE_RE = re.compile(
    r"(내시경|초음파|촬영|조영술|검진|골밀도|심전도|CT|MRI|엑스레이)\s*진료", re.IGNORECASE
)
_POSSIBLE_HOSPITAL_RE = re.compile(r"(진료\s*가능한|가능한)\s*병원")

# 증상·고민 문형(그룹 C)의 진료과 종류. 정규화한 진료과 이름 안의 표지로 고른다.
_KIND_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("치과", "dental"),
    ("한의", "korean_medicine"),
    ("한방", "korean_medicine"),
    ("산부인과", "obgyn"),
    ("여성의학", "obgyn"),
    ("피부", "dermatology"),
    ("정형외과", "orthopedics"),
    ("재활", "orthopedics"),
    ("마취통증", "orthopedics"),
    ("이비인후", "ent"),
    ("안과", "ophthalmology"),
    ("소아", "pediatrics"),
    ("비뇨", "urology"),
    ("신경과", "neurology"),
    ("내과", "internal"),
    ("가정의학", "internal"),
)

# (문형, 존댓말 여부는 문형 자체에 있다). {지역}은 매번 다른 지역 단위가 들어간다.
_SYMPTOM_TEMPLATES: Final[dict[str, tuple[str, ...]]] = {
    "internal": (
        "가슴이 두근거리는데 {지역}에서 어느 병원 가야 해?",
        "속이 자주 쓰리고 더부룩한데 {지역} {기관} 어디 가면 좋을까요?",
        "건강검진에서 간 수치가 높게 나왔는데 {지역}에서 다시 검사받을 만한 {기관} 있어?",
        "목이 붓고 쉽게 피곤해서 갑상선 검사를 받아보고 싶어요. {지역} 어디가 좋을까요?",
        "혈압이 계속 높게 나오는데 {지역} {기관} 추천해줘",
        "배가 자주 아프고 변비가 오래가는데 {지역}에서 대장 검사 받을 곳 있을까?",
        "기침이 2주째 안 멈춰요. {지역} {기관} 어디로 가야 할까요?",
        "당뇨 전 단계라는 말을 들었는데 {지역}에서 꾸준히 관리받을 {기관} 알려줘",
        "소화가 안 되고 체중이 줄었는데 {지역}에서 위내시경 되는 곳 있어?",
        "어지럽고 숨이 찬데 {지역}에서 검사 받을 수 있는 {기관} 있을까요?",
        "부모님이 요즘 기운이 없으셔서 {지역}에서 종합적으로 봐줄 {기관} 찾고 있어요",
    ),
    "dental": (
        "어금니가 시린데 {지역} {기관} 어디가 좋아?",
        "잇몸에서 자꾸 피가 나는데 {지역} {기관} 추천해줘",
        "사랑니가 아파서 빼야 할 것 같은데 {지역}에서 사랑니 잘 빼는 {기관} 있어?",
        "이가 깨졌는데 {지역}에 오늘 바로 볼 수 있는 {기관} 있을까요?",
        "찬물 마실 때 이가 찌릿한데 {지역} {기관} 어디 가야 해?",
        "앞니 색이 변해서 신경 쓰이는데 {지역}에서 상담받을 {기관} 알려줘",
        "입 냄새가 심해서 고민인데 {지역} {기관}에서 봐줄까?",
        "아이 충치 치료 때문에 {지역} {기관} 찾고 있어요",
        "턱에서 소리가 나고 입 벌릴 때 아파요. {지역} {기관} 어디로 가야 할까요?",
        "틀니가 잘 안 맞으시는 부모님 모시고 갈 {지역} {기관} 추천해줘",
        "잇몸이 내려앉은 것 같은데 {지역}에서 잇몸 치료 잘하는 {기관} 있어?",
    ),
    "dermatology": (
        "여드름 흉터 때문에 {지역} {기관} 알아보는 중이야",
        "얼굴에 기미가 늘었는데 {지역} {기관} 어디가 좋을까요?",
        "두피가 가렵고 각질이 많은데 {지역} {기관} 추천해줘",
        "손에 습진이 계속 생겨요. {지역}에서 어느 {기관} 가야 해?",
        "점을 빼고 싶은데 {지역} {기관} 중에 어디가 괜찮아?",
        "아이 아토피가 심해져서 {지역} {기관} 찾고 있어요",
        "발톱 무좀이 오래돼서 {지역}에서 치료받을 {기관} 알려줘",
        "갑자기 두드러기가 올라왔는데 {지역}에 오늘 진료하는 {기관} 있어?",
        "머리카락이 많이 빠져서 걱정인데 {지역} {기관} 상담받을 곳 있을까요?",
        "사마귀가 번지는 것 같아요. {지역} {기관} 어디로 가야 해?",
        "흉터가 오래 남아서 {지역}에서 흉터 치료 잘하는 {기관} 찾아",
    ),
    "korean_medicine": (
        "교통사고 나고 허리가 아픈데 {지역} {기관} 추천해줘",
        "손목이 시큰거리는데 {지역}에 침 잘 놓는 {기관} 있어?",
        "늘 몸이 차고 피곤해서 {지역} {기관}에서 상담받고 싶어요",
        "목이랑 어깨가 늘 뭉치는데 {지역} {기관} 어디가 좋아?",
        "발목을 삐었는데 {지역}에서 침 맞을 수 있는 {기관} 알려줘",
        "소화가 잘 안 되고 속이 더부룩해요. {지역} {기관} 가도 될까요?",
        "산후조리 때문에 {지역} {기관} 찾고 있어요",
        "아이가 자주 감기에 걸려서 {지역} {기관}에서 보약 상담받고 싶어",
        "허리 디스크 진단받았는데 {지역} {기관}에서 치료받아도 되나요?",
        "잠을 잘 못 자고 두근거려서 {지역} {기관} 알아보는 중이야",
        "무릎이 시려서 계단 오르기 힘든데 {지역} {기관} 추천 부탁해요",
    ),
    "obgyn": (
        "생리통이 심한데 {지역}에 여의사 있는 {기관} 있어?",
        "생리 주기가 불규칙한데 {지역} {기관} 어디가 좋을까요?",
        "질염이 자주 재발해서 {지역}에서 다닐 {기관} 찾고 있어",
        "임신 준비 중인데 {지역}에서 검사 받을 {기관} 알려줘",
        "자궁경부암 검사 받으려는데 {지역} {기관} 추천해줘",
        "아랫배가 묵직하게 아픈데 {지역} {기관} 가봐야 할까요?",
        "갱년기 증상 때문에 {지역}에서 상담받을 {기관} 있을까?",
        "유방 멍울이 만져져서 걱정인데 {지역}에서 검사받을 곳 있어?",
        "생리 양이 갑자기 많아졌는데 {지역} {기관} 어디로 가야 해?",
        "피임 상담을 받고 싶은데 {지역} {기관} 중에 편한 곳 알려줘",
        "임신 초기인데 {지역}에서 다니기 좋은 {기관} 찾고 있어요",
    ),
    "orthopedics": (
        "계단 내려갈 때 무릎이 아픈데 {지역} {기관} 어디가 좋아?",
        "어깨가 안 올라가는데 {지역}에서 어느 병원 가야 해?",
        "허리가 아파서 다리까지 저린데 {지역} {기관} 추천해줘",
        "손목이 시큰거리는데 {지역} {기관} 가봐야 할까요?",
        "발목을 접질렸는데 {지역}에 오늘 엑스레이 찍을 수 있는 곳 있어?",
        "목이 뻣뻣하고 두통까지 있어요. {지역}에서 어디로 가면 될까요?",
        "운동하다 다친 무릎 때문에 {지역} {기관} 알아보는 중이야",
        "발바닥이 아침마다 아픈데 {지역}에서 진료받을 곳 알려줘",
        "부모님 골다공증 검사 때문에 {지역} {기관} 찾고 있어요",
        "오래 앉아 있으면 허리가 끊어질 것 같은데 {지역} {기관} 어디 가?",
        "팔꿈치가 아파서 물건 들기 힘든데 {지역} {기관} 추천 부탁해요",
    ),
    "ent": (
        "코가 계속 막히고 콧물이 나는데 {지역} {기관} 어디가 좋아?",
        "목이 자주 쉬는데 {지역} {기관} 추천해줘",
        "귀에서 소리가 나는데 {지역}에서 어느 병원 가야 해?",
        "아이가 중이염이 자주 걸려서 {지역} {기관} 찾고 있어요",
        "코골이가 심해서 {지역}에서 검사받을 {기관} 있을까요?",
        "어지러움이 자주 오는데 {지역} {기관} 가봐도 될까?",
        "비염 때문에 잠을 못 자요. {지역} {기관} 알려줘",
        "편도가 자주 부어서 {지역} {기관} 상담받고 싶어",
        "목에 뭐가 걸린 느낌이 오래가는데 {지역} {기관} 어디로 가요?",
        "냄새를 잘 못 맡겠는데 {지역}에서 진료받을 {기관} 있어?",
        "축농증이 의심되는데 {지역} {기관} 추천 부탁해요",
    ),
}
# 진료과별 문형도, 질환·증상 항목도 없을 때 쓰는 진료과 이름만의 문형.
_GENERIC_CONCERNS: Final[tuple[str, ...]] = (
    "몸이 계속 안 좋은데 {지역}에서 어느 {기관} 가야 할까?",
    "처음 가보는 {기관}인데 {지역}에서 친절한 곳 있을까요?",
    "가족이 아파서 {지역} {기관} 알아보는 중이야",
    "증상이 오래가는데 {지역} {기관}에서 상담받고 싶어요",
    "{지역}에서 꾸준히 다닐 {기관} 추천해줘",
    "검사를 받아보고 싶은데 {지역} {기관} 어디가 좋아?",
    "부모님 모시고 갈 {지역} {기관} 찾고 있어요",
    "갑자기 아파서 그러는데 {지역}에 오늘 진료하는 {기관} 있어?",
    "{지역} {기관} 처음 가는데 어디가 괜찮을까요?",
    "아이랑 같이 갈 {지역} {기관} 알려줘",
    "{지역}에서 설명 잘해주는 {기관} 있을까?",
)
_GENERIC_SYMPTOMS: Final[tuple[str, ...]] = (
    "요즘 {증상} 때문에 고민인데 {지역} {기관} 어디가 좋아?",
    "{증상이} 오래가는데 {지역}에서 진료받을 {기관} 알려줘",
    "가족이 {증상} 때문에 힘들어해서 {지역} {기관} 찾고 있어요",
    "{지역}에서 {증상} 상담받을 수 있는 {기관} 있을까요?",
    "{증상이} 갑자기 심해졌는데 {지역} {기관} 어디로 가야 해?",
    "처음 {증상이} 생겼는데 {지역}에 친절한 {기관} 있어?",
    "{증상} 검사를 받아보고 싶은데 {지역} {기관} 추천해줘",
    "{지역}에서 {증상} 꾸준히 관리받을 {기관} 알려줘",
    "{증상} 때문에 잠을 못 자요. {지역} {기관} 어디가 괜찮을까요?",
    "부모님 {증상} 때문에 {지역}에서 모시고 갈 {기관} 찾고 있어",
)

_BRAND_TEMPLATES: Final[tuple[str, ...]] = (
    "{병원명} 어때?",
    "{병원명} 진료 시간 알려줘",
    "{병원명}에서 {시술} 해?",
    "{병원명} 위치가 어디예요?",
    "{병원명} 토요일에도 진료하나요?",
    "{병원명}에서 {질환} 진료도 봐요?",
    "{병원명} 주차 가능해?",
    "{병원명} 원장님 어떤 분이야?",
)

# B 진료·검사 탐색 — 진료과 문형과 항목 종류별 문형. {항목}은 진료 항목·키워드를 돌려 쓴다.
_EXPLORE_SPECIALTY: Final[tuple[str, ...]] = (
    "{지역} {기관} 추천해줘",
    "{지역}에 잘 보는 {기관} 있어?",
    "{지역} {기관} 어디가 좋을까요?",
    "{지역}에서 믿고 다닐 만한 {기관} 알려줘",
)
_EXPLORE_EXAM: Final[tuple[str, ...]] = (
    "{지역} {항목} 검사 되는 곳",
    "{지역}에서 {항목} 받을 수 있는 {기관} 알려줘",
    "{지역} {항목} 잘하는 곳 추천해줘",
    "{지역}에서 {항목} 받으려면 어디로 가야 해?",
)
_EXPLORE_PROCEDURE: Final[tuple[str, ...]] = (
    "{지역} {항목} 잘하는 {기관}",
    "{지역}에서 {항목} 받을 수 있는 {기관} 알려줘",
    "{지역} {항목} 잘하는 곳 추천해줘",
    "{지역}에서 {항목} 받으려면 어디가 좋아?",
)
_EXPLORE_DISEASE: Final[tuple[str, ...]] = (
    "{지역} {항목} 잘 보는 {기관}",
    "{지역}에서 {항목} 진료받을 {기관} 추천해줘",
    "{항목} 때문에 {지역} {기관} 찾고 있어요",
    "{지역}에 {항목} 잘 보는 곳 있을까요?",
)
# 사전에 없는 말(교통사고·후유증 같은 상황어 포함)은 '잘하는'·'받는'을 붙이지 않는다.
_EXPLORE_UNKNOWN: Final[tuple[str, ...]] = (
    "{항목} 때문에 {지역} {기관} 찾고 있어요",
    "{지역}에서 {항목} 상담받을 수 있는 {기관} 알려줘",
)

# D 상황·조건. {항목}은 시술·검사 항목만(비용·당일 문형).
_SITUATION_TEMPLATES: Final[tuple[str, ...]] = (
    "{동} 근처 토요일 진료하는 {기관}",
    "{동} 주차 되는 {기관} 있어?",
    "{지역} 야간 진료 {기관}",
    "{지역} {시술} 비용 괜찮은 곳",
    "{지역}에 일요일에도 문 여는 {기관} 있을까요?",
    "{지역}에서 여의사 진료 보는 {기관} 알려줘",
    "{동}에서 걸어서 갈 수 있는 {기관} 추천해줘",
    "{지역}에 점심시간에도 진료하는 {기관} 있어?",
    "{지역}에서 예약 없이 바로 갈 수 있는 {기관} 알려줘",
    "{지역} {시술} 당일에 가능한 곳 있을까요?",
    "{지역}에서 아이랑 같이 가기 편한 {기관} 알려줘",
)


@dataclass(frozen=True, slots=True)
class DraftQuestion:
    text: str
    group: str
    role: str | None  # FIXED / ROTATING / None(교체 후보)
    topic: str | None = None


def _clean(value: object) -> str:
    return normalize(str(value or "")).strip()


def common_clinic_name(specialty: str) -> str:
    """질문에 쓸 기관 이름 — '한의과' → '한의원', '내과 진료' → '내과', '일반의원' → '의원'."""

    text = re.sub(r"\s*진료$", "", _clean(specialty))
    if not text:
        return "병원"
    canonical = canonical_specialty(text) or text
    for key, name in _COMMON_CLINIC.items():
        if canonical == key or text == key:
            return name
    if "한의" in canonical or "한방" in canonical:
        return "한의원"
    return canonical


def specialty_kind(specialties: Sequence[str]) -> str | None:
    for specialty in specialties:
        text = _clean(specialty)
        for marker, kind in _KIND_MARKERS:
            if marker in text:
                return kind
    return None


def measurement_question_problems(text: object, *, clinic: str | None = None) -> list[str]:
    """측정 질문의 결함 코드 — 기존 문형 검사(`question_problems`) + 지침 3-1.

    기존 유료 매트릭스·시드·콘텐츠 브리프는 이 검사를 쓰지 않는다(그쪽 생성 결과·지문이 바뀌지
    않게). 30개 초안과 어드민 저장만 쓴다.
    """

    value = _clean(text)
    problems = list(question_problems(value))
    if len(value.replace(" ", "")) < 4:
        problems.append(PROBLEM_TOO_SHORT)
    if _REDUNDANT_INSTITUTION_RE.search(value):
        problems.append(PROBLEM_INSTITUTION_REDUNDANT)
    if _EXAM_AS_CARE_RE.search(value):
        problems.append(PROBLEM_EXAM_AS_CARE)
    if _POSSIBLE_HOSPITAL_RE.search(value):
        problems.append(PROBLEM_POSSIBLE_HOSPITAL)
    if clinic in ("치과", "한의원") and re.search(r"어느\s*병원|병원\s*(추천|알려|어디)", value):
        problems.append(PROBLEM_WRONG_INSTITUTION)
    return problems


def _regions(region: Sequence[str]) -> list[str]:
    """지역 단위 후보 — 병원 `region` 칸 값과 그 안의 넓은 단위(첫 낱말)를 섞어 쓴다."""

    values: list[str] = []
    for raw in region:
        text = _clean(raw)
        if not text:
            continue
        for candidate in (text, *text.split()):
            candidate = candidate.strip()
            if len(candidate) >= 2 and candidate not in values:
                values.append(candidate)
    return values or [""]


def _dong(regions: list[str]) -> str:
    for value in reversed(regions):
        if value.endswith(("동", "역", "가")):
            return value
    return regions[-1]


# 사전이 증상·질환으로 잘못 읽어도 받는 진료(서비스)인 말 — 증상 문형('…이 오래가는데')에 넣지 않는다.
_SERVICE_SUFFIXES: Final[tuple[str, ...]] = ("접종", "주사", "상담", "검진", "수액", "처방")
# 검색어 꼴 키워드에서 지역·진료과를 떼고 남은 군더더기 — 항목 이름으로 쓰지 않는다('위내시경 곳').
_SEARCH_RESIDUE_RE = re.compile(r"(^|\s)(곳|추천|잘하는|잘\s*하는|잘\s*보는|병원|의원|근처|가까운)(\s|$)")


@dataclass(frozen=True, slots=True)
class _Topic:
    term: str
    kind: KeywordClass
    exam: bool

    @property
    def procedural(self) -> bool:
        """'받는'·'잘하는'·'해?'가 붙어도 되는 항목 — 시술·진료 방식·검사·접종·주사."""

        return (
            self.exam
            or self.kind in (KeywordClass.PROCEDURE, KeywordClass.CARE_SERVICE)
            or self.term.endswith(_SERVICE_SUFFIXES)
        )

    @property
    def condition(self) -> bool:
        return not self.procedural and self.kind in (
            KeywordClass.DISEASE,
            KeywordClass.SYMPTOM,
            KeywordClass.BODY_PART,
        )


def _topics(treatments: Iterable[Any], keywords: Iterable[Any], region: Sequence[str]) -> list[_Topic]:
    names: list[str] = []
    for item in (*tuple(treatments or ()), *tuple(keywords or ())):
        value = item.get("name") if isinstance(item, Mapping) else item
        text = _clean(value)
        if len(text.replace(" ", "")) >= 2 and text not in names:
            names.append(text)
    topics: list[_Topic] = []
    for name in names:
        analysis = analyze_keyword(name, list(region))
        if analysis.keyword_class is KeywordClass.SEARCH_PHRASE:
            continue
        term = analysis.canonical_term or name
        if _SEARCH_RESIDUE_RE.search(term) or _SEARCH_RESIDUE_RE.search(name):
            continue
        if any(topic.term == term for topic in topics):
            continue
        topics.append(_Topic(term=term, kind=analysis.keyword_class, exam=is_exam_term(term)))
    return topics


def _explore_templates(topic: _Topic) -> tuple[str, ...]:
    if topic.exam:
        return _EXPLORE_EXAM
    if topic.procedural:
        return _EXPLORE_PROCEDURE
    if topic.condition:
        return _EXPLORE_DISEASE
    return _EXPLORE_UNKNOWN


def _subject(term: str) -> str:
    """'두통이'·'디스크가' — 받침에 맞춘 주격 조사."""

    last = term.strip()[-1:] if term.strip() else ""
    if "가" <= last <= "힣":
        return f"{term}{'이' if (ord(last) - 0xAC00) % 28 else '가'}"
    return f"{term}이"


def _render(template: str, **values: str) -> str:
    if "증상" in values:
        values = {**values, "증상이": _subject(values["증상"])}
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    text = re.sub(r"\s{2,}", " ", text).strip()
    # '갑상선초음파 검사 되는 곳'·'건강검진 검사'처럼 '검사'를 겹쳐 쓰지 않는다.
    return re.sub(r"(검사|검진)\s+검사", r"\1", text)


def _interleave(columns: Sequence[Sequence[tuple[str, str | None]]]) -> list[tuple[str, str | None]]:
    """열마다 한 줄씩 돌아가며 — 한 항목·한 문형이 앞자리를 몰아 차지하지 않게."""

    rows: list[tuple[str, str | None]] = []
    depth = max((len(column) for column in columns), default=0)
    for index in range(depth):
        for column in columns:
            if index < len(column):
                rows.append(column[index])
    return rows


def draft_measurement_set(
    *,
    hospital_name: str,
    region: Sequence[str],
    specialties: Sequence[str],
    keywords: Iterable[Any] = (),
    treatments: Iterable[Any] = (),
) -> list[DraftQuestion]:
    """30개 초안 + 그룹별 교체 후보. 같은 입력이면 늘 같은 결과다(어드민 '초안 다시 만들기').

    항목이 적거나 증상 문형이 없는 진료과면 30개를 다 채우지 못할 수 있다 — 모자란 만큼 비어
    있고, 마케터가 어드민에서 채운다(세트 유효 조건은 고정 15개 이상).
    """

    clinic = common_clinic_name(specialties[0] if specialties else "")
    regions = _regions(region)
    dong = _dong(regions)
    topics = _topics(treatments, keywords, region)
    kind = specialty_kind(specialties)
    name = _clean(hospital_name)
    procedural = [topic for topic in topics if topic.procedural]
    conditions = [topic for topic in topics if topic.condition]
    region_cursor = 0

    def region_at() -> str:
        nonlocal region_cursor
        value = regions[region_cursor % len(regions)]
        region_cursor += 1
        return value

    # A 병원명 — 시술 항목·질환 항목이 있을 때만 그 문형을 쓴다.
    brand: list[tuple[str, str | None]] = []
    for index, template in enumerate(_BRAND_TEMPLATES):
        if "{시술}" in template:
            if not procedural:
                continue
            topic = procedural[index % len(procedural)]
            brand.append((_render(template, 병원명=name, 시술=topic.term), topic.term))
        elif "{질환}" in template:
            if not conditions:
                continue
            topic = conditions[index % len(conditions)]
            brand.append((_render(template, 병원명=name, 질환=topic.term), topic.term))
        else:
            brand.append((_render(template, 병원명=name), None))

    # B 진료·검사 탐색 — 진료과 문형 한 열 + 항목마다 한 열.
    explore_columns: list[list[tuple[str, str | None]]] = [
        [(_render(template, 지역=region_at(), 기관=clinic), None) for template in _EXPLORE_SPECIALTY]
    ]
    for topic in topics:
        explore_columns.append(
            [
                (_render(template, 지역=region_at(), 항목=topic.term, 기관=clinic), topic.term)
                for template in _explore_templates(topic)
            ]
        )
    explore = _interleave(explore_columns)

    # C 증상·고민 — 진료과별 문형, 없으면 질환·증상 항목으로 기본 문형.
    symptom: list[tuple[str, str | None]] = []
    pool = _SYMPTOM_TEMPLATES.get(kind or "")
    if pool:
        symptom = [(_render(template, 지역=region_at(), 기관=clinic), None) for template in pool]
    elif not conditions:
        symptom = [
            (_render(template, 지역=region_at(), 기관=clinic), None) for template in _GENERIC_CONCERNS
        ]
    else:
        symptom = [(_render(_GENERIC_CONCERNS[0], 지역=region_at(), 기관=clinic), None)] + _interleave(
            [
                [
                    (_render(template, 지역=region_at(), 증상=topic.term, 기관=clinic), topic.term)
                    for template in _GENERIC_SYMPTOMS
                ]
                for topic in conditions
            ]
        )

    # D 상황·조건 — 비용·당일 문형은 시술·검사 항목에만.
    situation: list[tuple[str, str | None]] = []
    for index, template in enumerate(_SITUATION_TEMPLATES):
        if "{시술}" in template:
            if not procedural:
                continue
            topic = procedural[index % len(procedural)]
            situation.append(
                (_render(template, 지역=region_at(), 시술=topic.term), topic.term)
            )
        else:
            situation.append((_render(template, 지역=region_at(), 동=dong, 기관=clinic), None))

    seen: set[str] = set()
    drafted: list[DraftQuestion] = []
    for group, candidates in (("A", brand), ("B", explore), ("C", symptom), ("D", situation)):
        fixed, rotating = GROUP_PLAN[group]
        need = fixed + rotating + (EXTRA_CANDIDATES if rotating else 0)
        picked: list[tuple[str, str | None]] = []
        for text, topic in candidates:
            if len(picked) >= need:
                break
            if not text or text in seen or measurement_question_problems(text, clinic=clinic):
                continue
            seen.add(text)
            picked.append((text, topic))
        for index, (text, topic) in enumerate(picked):
            if index < fixed:
                role: str | None = ROLE_FIXED
            elif index < fixed + rotating:
                role = ROLE_ROTATING
            else:
                role = None
            drafted.append(DraftQuestion(text=text, group=group, role=role, topic=topic))
    return drafted

"""Map lead-magnet intake fields to a canonical query set (max 5).

Intake gives us three measurement fields — 병원명 / 지역 키워드 / 핵심 키워드 —
plus contact details that never touch the queries. 병원명 is deliberately kept
out of the queries: asking "장편한외과 어때?" guarantees a mention and measures
nothing. It is used only to judge whether the answer named the hospital.

Query shapes are the two patients actually use, with no superlatives:
    시술·검사형   {지역} 근처 {키워드} 병원 추천해줘
    증상·질환형   {키워드}가 있는데 {지역} 근처 병원 어디로 가야해?
    진료과형      {지역} 근처 {진료과} 병원 추천해줘

Measured evidence behind these choices (artifacts/competitor-2026-07-28):
  · templates without a region slot returned zero hospital names — dropped
  · superlative variants ("잘하는", "소문난") sampled variance, not coverage
  · 4 queries × 3 repeats separated the top hospitals cleanly at 12 calls
"""
from __future__ import annotations

import re

MAX_QUERIES = 5

# 키워드가 시술·검사인지 증상·질환인지에 따라 환자의 말투가 달라진다.
PROCEDURE_RE = re.compile(
    r"내시경|수술|시술|성형|교정|이식|절제|주사|레이저|검진|검사|도수|재활|스케일링|임플란트"
)
CONDITION_RE = re.compile(
    r"디스크|염$|염\b|통증|골절|탈장|결석|종양|암$|증후군|장애|무릎|허리|어깨|목디스크"
)

# 키워드 → 진료과. 공개 데이터(심평원)로 대체하기 전까지 쓰는 최소 사전.
SPECIALTY_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"대장|위|내시경|소화|간|담낭|역류|장염"), "내과"),
    (re.compile(r"치질|치루|치열|탈장|담석|맹장|유방|갑상선"), "외과"),
    (re.compile(r"디스크|척추|허리|목|어깨|무릎|관절|골절|인대"), "정형외과"),
    (re.compile(r"피부|여드름|점|레이저|보톡스|필러|탈모"), "피부과"),
    (re.compile(r"임플란트|충치|교정|스케일링|치아|잇몸"), "치과"),
    (re.compile(r"백내장|라식|라섹|시력|녹내장"), "안과"),
    (re.compile(r"코|비염|축농증|중이염|편도|귀"), "이비인후과"),
    (re.compile(r"산부인|자궁|난임|출산|생리"), "산부인과"),
    (re.compile(r"소아|아이|영유아|예방접종"), "소아청소년과"),
    (re.compile(r"우울|불안|불면|공황|adhd|ADHD"), "정신건강의학과"),
]


def has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는지 — 조사 '이/가' 선택에 필요."""
    if not word:
        return False
    last = word.strip()[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - 0xAC00) % 28 != 0


def subject_particle(word: str) -> str:
    return "이" if has_final_consonant(word) else "가"


def normalize_region(region: str) -> str:
    """'수서역', '성수동', '강남구' 모두 '{R} 근처'로 자연스럽게 붙는다."""
    return re.sub(r"\s+", " ", region).strip()


def infer_specialty(keywords: list[str]) -> str | None:
    joined = " ".join(keywords)
    for pattern, specialty in SPECIALTY_MAP:
        if pattern.search(joined):
            return specialty
    return None


def classify(keyword: str) -> str:
    """시술·검사형이면 procedure, 증상·질환형이면 condition."""
    if PROCEDURE_RE.search(keyword):
        return "procedure"
    if CONDITION_RE.search(keyword):
        return "condition"
    # 판단이 안 서면 추천형이 안전하다 — 증상형은 어색한 문장이 되기 쉽다.
    return "procedure"


def build_queries(
    region: str, keywords: list[str], *, max_queries: int = MAX_QUERIES
) -> list[dict[str, str]]:
    """지역·키워드로 정규 질의 세트를 만든다. 병원명은 절대 넣지 않는다."""
    region = normalize_region(region)
    keywords = [k.strip() for k in keywords if k and k.strip()]
    if not region or not keywords:
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, text: str) -> None:
        if text not in seen and len(out) < max_queries:
            seen.add(text)
            out.append({"kind": kind, "query": text})

    # 진료과형 1개를 먼저 확보한다 — 키워드가 빗나가도 진료과는 지역 신호를 잡는다.
    specialty = infer_specialty(keywords)
    if specialty:
        add("진료과형", f"{region} 근처 {specialty} 병원 추천해줘")

    # 키워드별로 환자 말투에 맞는 형태 하나씩.
    for keyword in keywords:
        if classify(keyword) == "condition":
            particle = subject_particle(keyword)
            add("증상형", f"{keyword}{particle} 있는데 {region} 근처 병원 어디로 가야해?")
        else:
            add("시술형", f"{region} 근처 {keyword} 병원 추천해줘")

    # 키워드가 하나뿐이라 슬롯이 남으면 같은 키워드의 반대 형태를 하나 더.
    if len(out) < max_queries and len(keywords) == 1:
        keyword = keywords[0]
        if classify(keyword) == "condition":
            add("시술형", f"{region} 근처 {keyword} 병원 추천해줘")
        else:
            # "{시술} 때문에"는 환자가 쓰지 않는 말투다. 시술은 '받는' 것으로 묻는다.
            add("탐색형", f"{keyword} 받으려는데 {region} 근처 병원 어디가 좋아?")

    return out


if __name__ == "__main__":
    cases = [
        ("수서역", ["대장내시경"]),
        ("성수동", ["치질 수술"]),
        ("정자동", ["허리디스크"]),
        ("수서역", ["대장내시경", "치질 수술", "허리디스크"]),
        ("강남구", ["임플란트", "치아교정"]),
    ]
    for region, keywords in cases:
        print(f"\n입력  지역={region}  키워드={keywords}")
        specialty = infer_specialty(keywords)
        print(f"  유추 진료과: {specialty or '(없음)'}")
        for i, item in enumerate(build_queries(region, keywords), 1):
            print(f"  {i}. [{item['kind']}] {item['query']}")

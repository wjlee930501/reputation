"""핵심 키워드 분석 — 원장이 적은 단어가 무엇인지 판별한다.

## 왜 분리했는가

질의 생성기가 키워드를 `procedure | condition | unknown` 3종으로만 나누다 보니,
프로덕션 실측에서 **16개 중 13개가 unknown**으로 떨어졌다(척추·관절·치질·우울증·
불안증·불면증·피지낭종·지방종·내성발톱 …). unknown 폴백은 "{키워드} 받으려는데"였고,
그 결과 실제 신청자에게 나간 질의가 이랬다:

    "척추 받으려는데 보라매역 근처 병원 어디가 좋아?"
    "우울증 받으려는데 용산역 근처 병원 어디가 좋아?"

환자가 쓰지 않는 말이다. 리포트는 질의 원문을 **그대로 공개**하므로 원장이 이 문장을
보면 측정 전체의 신뢰가 무너진다. 그리고 코드에 있던 증상형 템플릿은 실전에서
**한 번도 발화되지 않았다.**

## 왜 LLM을 쓰지 않는가

질의는 접수 API 요청 경로에서 동기 생성된다. 여기에 외부 호출을 넣으면 폼 제출이
공급자 지연에 묶이고, 같은 입력이 다른 질의를 만들 수 있어 재현성 계약이 깨진다.
무엇보다 이건 의미 이해가 부족해서 생긴 문제가 아니라 **분류 체계가 좁고 폴백이
비문을 만들어서** 생긴 문제다. UNKNOWN에도 문법적으로 온전한 템플릿이 있으면
분류 실패는 장애가 아니라 품질 저하로 끝난다.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class KeywordClass(str, Enum):
    PROCEDURE = "PROCEDURE"        # 대장내시경, PRP주사 — 환자가 "받는" 것
    DISEASE = "DISEASE"            # 우울증, 치질, 지방종 — 환자가 "진료받는" 병
    SYMPTOM = "SYMPTOM"            # 통증, 두통 — 환자가 "겪는" 것
    BODY_PART = "BODY_PART"        # 척추, 관절 — 부위만 적은 경우
    CARE_SERVICE = "CARE_SERVICE"  # 비수술, 도수치료 — 진료 방식
    SEARCH_PHRASE = "SEARCH_PHRASE"  # "군자역 정형외과" — 이미 검색어 형태
    UNKNOWN = "UNKNOWN"


HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


@dataclass(frozen=True, slots=True)
class KeywordAnalysis:
    """키워드 1개의 분석 결과.

    `semantic_key`가 핵심이다 — 문자열이 달라도 같은 질문이 되는 후보를 걸러낸다.
    "군자역 정형외과"는 진료과 앵커 슬롯과 같은 키를 갖게 되어 탈락한다.
    """

    raw: str
    canonical_term: str
    keyword_class: KeywordClass
    confidence: str
    classifier_source: str
    semantic_key: str
    embedded_region: str | None = None
    embedded_specialty: str | None = None


# ── 정규화 ────────────────────────────────────────────────────────
# 대소문자·전각·구두점을 흡수한다. 이게 없으면 'adhd'와 'ADHD'와 'ＡＤＨＤ'가
# 서로 다른 후보로 슬롯을 각각 차지한다.
_PUNCT_RE = re.compile(r"[^\w\s가-힣]+", re.UNICODE)


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "").strip()
    folded = _PUNCT_RE.sub(" ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _match_key(text: str) -> str:
    return normalize(text).lower().replace(" ", "")


# ── 정확 일치 사전 ────────────────────────────────────────────────
# 형태 규칙보다 **먼저** 본다. 규칙은 넓게 잡으면 오분류를 만들지만 사전은 틀리지 않는다.
# 운영 중 UNKNOWN 비율과 실제 입력 상위값을 보고 검토된 표현만 여기 추가한다.
_LEXICON: dict[str, tuple[KeywordClass, str]] = {
    # 시술·검사
    "대장내시경": (KeywordClass.PROCEDURE, "대장내시경"),
    "위내시경": (KeywordClass.PROCEDURE, "위내시경"),
    "건강검진": (KeywordClass.PROCEDURE, "건강검진"),
    "prp주사": (KeywordClass.PROCEDURE, "PRP주사"),
    "체외충격파": (KeywordClass.PROCEDURE, "체외충격파 치료"),
    "임플란트": (KeywordClass.PROCEDURE, "임플란트"),
    "라식": (KeywordClass.PROCEDURE, "라식"),
    "라섹": (KeywordClass.PROCEDURE, "라섹"),
    # 질환
    "우울증": (KeywordClass.DISEASE, "우울증"),
    "불안증": (KeywordClass.DISEASE, "불안증"),
    "불면증": (KeywordClass.DISEASE, "불면증"),
    "공황장애": (KeywordClass.DISEASE, "공황장애"),
    "adhd": (KeywordClass.DISEASE, "ADHD"),
    "치질": (KeywordClass.DISEASE, "치질"),
    "치루": (KeywordClass.DISEASE, "치루"),
    "치열": (KeywordClass.DISEASE, "치열"),
    "탈장": (KeywordClass.DISEASE, "탈장"),
    "피지낭종": (KeywordClass.DISEASE, "피지낭종"),
    "지방종": (KeywordClass.DISEASE, "지방종"),
    "내성발톱": (KeywordClass.DISEASE, "내성발톱"),
    "디스크": (KeywordClass.DISEASE, "디스크"),
    "허리디스크": (KeywordClass.DISEASE, "허리디스크"),
    "목디스크": (KeywordClass.DISEASE, "목디스크"),
    "협착증": (KeywordClass.DISEASE, "척추관협착증"),
    "여드름": (KeywordClass.DISEASE, "여드름"),
    "아토피": (KeywordClass.DISEASE, "아토피"),
    "탈모": (KeywordClass.DISEASE, "탈모"),
    "무좀": (KeywordClass.DISEASE, "무좀"),
    "백내장": (KeywordClass.DISEASE, "백내장"),
    "녹내장": (KeywordClass.DISEASE, "녹내장"),
    "하지정맥류": (KeywordClass.DISEASE, "하지정맥류"),
    "오십견": (KeywordClass.DISEASE, "오십견"),
    "당뇨": (KeywordClass.DISEASE, "당뇨"),
    "고혈압": (KeywordClass.DISEASE, "고혈압"),
    # 증상
    "통증": (KeywordClass.SYMPTOM, "통증"),
    "두통": (KeywordClass.SYMPTOM, "두통"),
    "복통": (KeywordClass.SYMPTOM, "복통"),
    "요통": (KeywordClass.SYMPTOM, "요통"),
    "어지럼증": (KeywordClass.SYMPTOM, "어지럼증"),
    "소화불량": (KeywordClass.SYMPTOM, "소화불량"),
    # 부위
    "척추": (KeywordClass.BODY_PART, "척추"),
    "관절": (KeywordClass.BODY_PART, "관절"),
    "무릎": (KeywordClass.BODY_PART, "무릎"),
    "어깨": (KeywordClass.BODY_PART, "어깨"),
    "허리": (KeywordClass.BODY_PART, "허리"),
    "손목": (KeywordClass.BODY_PART, "손목"),
    "발목": (KeywordClass.BODY_PART, "발목"),
    "고관절": (KeywordClass.BODY_PART, "고관절"),
    # 진료 방식
    "비수술": (KeywordClass.CARE_SERVICE, "비수술 치료"),
    "도수치료": (KeywordClass.CARE_SERVICE, "도수치료"),
    "재활": (KeywordClass.CARE_SERVICE, "재활 치료"),
    "물리치료": (KeywordClass.CARE_SERVICE, "물리치료"),
}

# ── 형태 규칙 (사전에 없을 때만) ──────────────────────────────────
# 순서가 의미를 가진다. 시술을 먼저 봐야 "종양절제술"이 질환으로 새지 않는다.
_SUFFIX_RULES: tuple[tuple[re.Pattern[str], KeywordClass], ...] = (
    (re.compile(r"(내시경|수술|시술|성형|교정|이식|절제|주사|레이저|검진|검사|스케일링)$"),
     KeywordClass.PROCEDURE),
    (re.compile(r"(치료|요법|재활)$"), KeywordClass.CARE_SERVICE),
    (re.compile(r"(증후군|장애|결석|골절|탈장)$"), KeywordClass.DISEASE),
    # `증`은 증상이 아니라 질환에 붙는 일이 훨씬 많다(협착증·골다공증·역류증).
    # 증상형으로 접으면 "척추관협착증이 계속되는데"처럼 어색해진다.
    (re.compile(r"(염|암|종|질|증)$"), KeywordClass.DISEASE),
    (re.compile(r"(통|림)$"), KeywordClass.SYMPTOM),
)

# ── 구조 파싱: "군자역 정형외과" 같은 검색어 형태 ─────────────────
_REGION_SUFFIX_RE = re.compile(r"(역|동|구|시|군|읍|면|가)$")

# 진료과 별칭 정규화. 원장이 "정신과"라 적고 폼 진료과가 "정신건강의학과"여도
# 같은 것으로 봐야 중복이 잡힌다.
_SPECIALTY_ALIASES: dict[str, str] = {
    "정신과": "정신건강의학과",
    "정신건강의학과": "정신건강의학과",
    "비뇨기과": "비뇨의학과",
    "비뇨의학과": "비뇨의학과",
    "소아과": "소아청소년과",
    "소아청소년과": "소아청소년과",
    "항문외과": "대장항문외과",
    "대장항문외과": "대장항문외과",
}
_SPECIALTY_SUFFIX_RE = re.compile(r"(과|의원|병원|클리닉|한의원)$")


def canonical_specialty(text: str) -> str:
    key = _match_key(text)
    return _SPECIALTY_ALIASES.get(key, key)


def clinic_phrase(specialty: str) -> str:
    """"{진료과} 병원" 자리에 넣을 표현.

    폼의 진료과 칸에는 '내과'만 오지 않는다 — 실제로 '일반의원', '내과 진료',
    '소아청소년 진료' 같은 값이 들어온다. 템플릿에 그대로 끼우면
    `"경산 일반의원 병원 추천해줘"`처럼 기관어가 겹친다.

    '내과' → '내과 병원' · '일반의원' → '일반의원' · '내과 진료' → '내과 병원'
    """
    text = re.sub(r"\s*진료$", "", normalize(specialty))
    if not text:
        return "병원"
    # 이미 기관을 가리키는 말이면 '병원'을 덧붙이지 않는다. 단 '내과'처럼 '과'로
    # 끝나는 것은 진료과목이지 기관이 아니다.
    if _SPECIALTY_SUFFIX_RE.search(text) and not text.endswith("과"):
        return text
    return f"{text} 병원"


def _looks_like_region(token: str) -> bool:
    return len(token) >= 2 and bool(_REGION_SUFFIX_RE.search(token))


def _looks_like_specialty(token: str) -> bool:
    return len(token) >= 2 and bool(_SPECIALTY_SUFFIX_RE.search(token))


def _known_region_keys(regions: Iterable[str] | None) -> set[str]:
    keys: set[str] = set()
    for raw in regions or []:
        normalized = normalize(raw)
        for value in (normalized, *normalized.split()):
            key = _match_key(value)
            if not key:
                continue
            keys.add(key)
            stripped = re.sub(r"(특별자치시|특별자치도|광역시|특별시|시|군|구|읍|면|동|역)$", "", key)
            if len(stripped) >= 2:
                keys.add(stripped)
    return keys


def _split_structure(
    text: str,
    known_regions: Iterable[str] | None = None,
) -> tuple[str | None, str | None, str]:
    """'군자역 정형외과 PRP주사' → (군자역, 정형외과, 'PRP주사').

    잔여어가 남으면 그것을 다시 분류한다 — 지역·진료과를 지우는 것이 아니라
    **떼어내고 남은 임상 개념**을 보는 것이 요점이다.
    """
    region: str | None = None
    specialty: str | None = None
    residue: list[str] = []
    region_keys = _known_region_keys(known_regions)
    for token in normalize(text).split():
        if _match_key(token) in region_keys or _looks_like_region(token):
            region = region or token
        elif _looks_like_specialty(token):
            specialty = specialty or token
        else:
            residue.append(token)
    return region, specialty, " ".join(residue)


def _classify_term(term: str) -> tuple[KeywordClass, str, str, str]:
    """(클래스, 표준어, 신뢰도, 출처)."""
    key = _match_key(term)
    if not key:
        return KeywordClass.UNKNOWN, term, LOW, "fallback"
    hit = _LEXICON.get(key)
    if hit:
        return hit[0], hit[1], HIGH, "exact_lexicon"
    for pattern, klass in _SUFFIX_RULES:
        if pattern.search(key):
            return klass, normalize(term), MEDIUM, "suffix_rule"
    return KeywordClass.UNKNOWN, normalize(term), LOW, "fallback"


def analyze_keyword(
    raw: str,
    known_regions: Iterable[str] | None = None,
) -> KeywordAnalysis:
    """키워드 1개를 분석한다. **절대 예외를 던지지 않는다** — 접수를 막으면 리드가 죽는다."""
    region, specialty, residue = _split_structure(raw, known_regions)

    # 지역·진료과만 있고 임상 개념이 없으면 검색어 형태다. 이건 진료과 앵커 슬롯과
    # 같은 질문이라 그대로 쓰면 3개 질의가 사실상 1개가 된다.
    if (region or specialty) and not residue:
        canonical = canonical_specialty(specialty or "")
        return KeywordAnalysis(
            raw=raw,
            canonical_term=normalize(raw),
            keyword_class=KeywordClass.SEARCH_PHRASE,
            confidence=HIGH,
            classifier_source="structural",
            # 앵커와 같은 키 → 중복으로 걸러진다.
            semantic_key=f"specialty:{canonical}" if canonical else "specialty:",
            embedded_region=region,
            embedded_specialty=specialty,
        )

    target = residue or normalize(raw)
    klass, canonical, confidence, source = _classify_term(target)
    if (region or specialty) and source != "fallback":
        # 지역·진료과가 섞인 입력에서 임상 개념을 건져낸 경우에만 구조 파싱이 기여했다.
        # 평범한 단일 키워드에까지 이 표시를 붙이면 감사 로그가 거짓말을 한다.
        source = f"structural+{source}"
    return KeywordAnalysis(
        raw=raw,
        canonical_term=canonical,
        keyword_class=klass,
        confidence=confidence,
        classifier_source=source,
        semantic_key=f"{klass.value.lower()}:{_match_key(canonical)}",
        embedded_region=region,
        embedded_specialty=specialty,
    )


def lexicon_fingerprint() -> str:
    """사전·규칙의 지문. 측정 정책 스냅샷에 넣어 사전이 바뀐 것을 감지한다."""
    material = "|".join(
        f"{key}={value[0].value}:{value[1]}" for key, value in sorted(_LEXICON.items())
    )
    rules = "|".join(pattern.pattern for pattern, _ in _SUFFIX_RULES)
    return hashlib.sha256(f"{material}#{rules}".encode()).hexdigest()[:16]

"""자동 운영 요약(Essence)의 문장 단위 근거 대조 — 결정적 어휘 대조(Phase 4 D33).

근거 노트 id만 맞으면 통과시키면 "진료 전에 충분히 설명합니다"에 묶인 "본원의 주사 치료는 암세포를
제거합니다"가 필수 문구·공개 소개가 된다. 그래서 합성 문장마다 핵심 내용어가 연결된 노트 본문에
실제로 있는지 대조한다. 의료 효능·결과·자격·비교·수치 주장은 더 엄격하게 본다 — 주장 낱말과 수치가
노트에 그대로 있어야 하고 내용어 대부분이 겹쳐야 한다. 모델을 부르지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[가-힣A-Za-z]+|[0-9]+(?:[.,][0-9]+)?")
# 긴 어미·조사부터 뗀다. 한 낱말에서 두 번까지 떼어 "설명하고"·"설명드립니다"가 "설명"이 된다.
_SUFFIXES = tuple(
    sorted(
        {
            "하겠습니다", "드리겠습니다", "드립니다", "했습니다", "합니다", "됩니다", "입니다",
            "습니다", "ㅂ니다", "해요", "이에요", "예요", "하는", "하고", "하며", "하여", "해서",
            "하게", "하기", "한다", "된다", "되는", "되어", "되고", "하신", "하시", "드리", "드려",
            "에서는", "으로는", "에게는", "에서", "에게", "으로", "까지", "부터", "처럼", "이나",
            "이며", "이고", "라도", "에는", "와는", "과는", "께서", "은", "는", "이", "가", "을",
            "를", "의", "에", "와", "과", "도", "만", "로", "고", "며", "한", "할", "된", "될",
            "히", "게", "요", "다", "인",
        },
        key=len,
        reverse=True,
    )
)
_VERB_TAILS = ("하", "되", "시키")
_STOPWORDS = frozenset(
    {
        "본원", "병원", "의원", "저희", "우리", "원장", "원장님", "환자", "환자분", "진료", "경우",
        "모든", "항상", "함께", "위해", "대한", "통해", "그리고", "또한", "있", "없", "있습", "없습",
        "관련", "정도", "부분", "이번", "통한", "위한", "같은", "다른", "여러", "드리", "하",
        # 결정적 폴백 템플릿의 머리말 — 근거 발췌가 아니라 틀이다.
        "자료", "확인", "핵심", "메시지", "근거", "범위", "제한", "약속", "표현", "기준", "문체",
        "원문", "유지", "항목", "기반", "말할",
    }
)
# 의료 효능·결과·보장·비교 주장 어휘(한국어 활용형 어간 + 영문). **한 곳의 정본**이다 — 자동 판의
# 필수 문구·공개 소개 선택, 합성 문장 정리, 필수 문구 지적 면제 판정이 모두 이 목록을 쓴다.
# 미검출을 안전으로 보지 않도록 넓게 잡는다(오탐은 문장을 버릴 뿐이다, fail-closed).
MEDICAL_CLAIM_LEXICON: tuple[str, ...] = (
    # 효능·결과(활용형 어간)
    "효과", "효능", "개선", "완화", "회복", "제거", "완치", "치유", "호전", "재발", "성공", "예방",
    "억제", "해결", "근본", "낫", "나아", "나았", "나을", "좋아지", "좋아집", "좋아져", "사라지",
    "사라집", "사라져", "사라졌", "없애", "없앱", "없앴", "줄여", "줄이", "줄입", "고치", "고쳐",
    "고칩", "치료합니다", "치료됩니다", "치료해", "치료할 수", "치료 효과", "통증 없", "흉터 없",
    "부작용 없", "안전합니다", "회복됩니다",
    # 보장·단정
    "보장", "확실", "반드시", "영구", "평생", "100%", "백퍼",
    # 비교·최상급
    "최초", "유일", "최고", "최상", "가장", "1위", "최다", "최신", "우수", "탁월", "독보", "뛰어난",
    "더 나은", "보다 나은", "넘버원",
    # 영문
    "cure", "curing", "eliminat", "remov", "heal", "effective", "efficac", "guarantee",
    "permanent", "recover", "prevent", "relief", "relieve", "painless", "no side effect",
    "best", "#1", "number one", "first", "only",
)
# 필수 문구 지적을 기록용으로 내리지 못하게 하는 안전 어휘(어휘 목록과 함께 쓴다).
SAFETY_TERMS: tuple[str, ...] = (
    "응급", "119", "용량", "dosage", "dose", "금기", "contraindicat", "부작용", "side effect",
    "합병증", "complication", "효능", "efficacy", "unsubstantiated", "unsafe", "치료 효과",
)
# 수치 주장 — 아라비아 숫자와 한국어 수사+단위("한 번"·"백 번"·"수백 명").
_QUANTITY = re.compile(
    r"[0-9]+(?:[.,][0-9]+)?\s*(?:%|퍼센트|년|명|건|회|번|배|위|cm|mm|kg|일|개월|주|시간|분|살|세)"
    r"|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|백|천|만|수십|수백|수천|수만)\s*"
    r"(?:번|회|명|건|배|년|개월|일|시간|분|살|세|가지|차례)"
)
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])(?![0-9])\s*")
# 결정적 폴백 문장의 틀 머리말. 대조는 콜론 뒤의 근거 발췌로 한다.
_TEMPLATE_PREFIXES = (
    "자료에서 확인된 핵심 메시지:",
    "환자에게 말할 수 있는 약속은 이 근거 범위로 제한:",
    "자료 표현 기준 문체:",
    "근거 문장을 벗어나지 않고 설명합니다:",
    "원문 톤을 유지합니다:",
)

GENERAL_SUPPORT_RATIO = 0.5
CLAIM_SUPPORT_RATIO = 0.8


def _strip_suffix(word: str) -> str:
    for _ in range(2):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 1:
                word = word[: -len(suffix)]
                break
        else:
            break
    for tail in _VERB_TAILS:
        if word.endswith(tail) and len(word) - len(tail) >= 2:
            word = word[: -len(tail)]
            break
    return word


def content_stems(text: str) -> list[str]:
    stems: list[str] = []
    for match in _WORD.finditer(text or ""):
        word = match.group(0)
        stem = word if word[0].isdigit() else _strip_suffix(word)
        if len(stem) < 2 or stem in _STOPWORDS:
            continue
        stems.append(stem)
    return list(dict.fromkeys(stems))


def without_template_prefix(text: str) -> str:
    stripped = (text or "").strip()
    for prefix in _TEMPLATE_PREFIXES:
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return stripped


def claim_words(text: str) -> list[str]:
    """문장에 든 의료 효능·결과·보장·비교 어휘와 수치(`MEDICAL_CLAIM_LEXICON`)."""

    lowered = (text or "").lower()
    found = [word for word in MEDICAL_CLAIM_LEXICON if word in lowered]
    found.extend(match.group(0) for match in _QUANTITY.finditer(text or ""))
    return found


def is_medical_claim(text: str) -> bool:
    return bool(claim_words(text))


def has_critical_terms(*texts: str | None) -> bool:
    """의료 주장 어휘나 안전 어휘가 하나라도 들었는가(필수 문구 지적 면제의 fail-closed 판정)."""

    joined = " ".join(text for text in texts if text).lower()
    return bool(claim_words(joined)) or any(term in joined for term in SAFETY_TERMS)


def normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().strip("\"'“”‘’「」『』").strip()


def is_verbatim_in(sentence: str, texts: Iterable[str]) -> bool:
    """문장이 어느 근거 본문에 글자 그대로(공백만 정규화) 있는가."""

    needle = normalize_sentence(sentence)
    if not needle:
        return False
    return any(needle in normalize_sentence(text) for text in texts if text)


@dataclass(frozen=True)
class _Support:
    stems: frozenset[str]
    blob: str
    blob_nospace: str


def _support(texts: Iterable[str]) -> _Support:
    joined = " ".join(text for text in texts if text)
    return _Support(
        stems=frozenset(content_stems(joined)),
        blob=joined,
        blob_nospace=re.sub(r"\s+", "", joined),
    )


def _stem_matches(stem: str, support: _Support) -> bool:
    if stem in support.stems:
        return True
    if len(stem) >= 3 and stem in support.blob_nospace:
        return True
    return any(
        min(len(stem), len(other)) >= 2 and (other.startswith(stem) or stem.startswith(other))
        for other in support.stems
    )


def support_ratio(text: str, support_texts: Iterable[str]) -> float:
    stems = content_stems(without_template_prefix(text))
    if not stems:
        return 1.0
    support = _support(support_texts)
    return sum(_stem_matches(stem, support) for stem in stems) / len(stems)


def claim_is_supported(
    text: str,
    support_texts: Iterable[str],
    *,
    require_general: bool = True,
) -> bool:
    """문장이 연결된 근거 노트 본문으로 뒷받침되는가.

    `require_general=False`(목소리·원칙 같은 문체 묘사)면 의료 주장일 때만 대조한다.
    """

    body = without_template_prefix(text)
    texts = [item for item in support_texts if item]
    medical = is_medical_claim(body)
    if not medical and not require_general:
        return True
    if not texts:
        return False
    ratio = support_ratio(body, texts)
    if not medical:
        return ratio >= GENERAL_SUPPORT_RATIO
    support = _support(texts)
    words_present = all(
        word in support.blob or re.sub(r"\s+", "", word) in support.blob_nospace
        for word in claim_words(body)
    )
    return words_present and ratio >= CLAIM_SUPPORT_RATIO


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(text or "") if part.strip()]


__all__ = (
    "CLAIM_SUPPORT_RATIO",
    "GENERAL_SUPPORT_RATIO",
    "claim_is_supported",
    "MEDICAL_CLAIM_LEXICON",
    "SAFETY_TERMS",
    "claim_words",
    "content_stems",
    "has_critical_terms",
    "is_medical_claim",
    "is_verbatim_in",
    "normalize_sentence",
    "split_sentences",
    "support_ratio",
    "without_template_prefix",
)

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

#: 자동 판을 만든 정리 정책의 판. 이 값이 바뀌면 옛 정책으로 만든 자동 판은 다음 정기 조정에서
#: 다시 만들어지고(T7), 그 전에도 소비 단계(`must_use_verbatim`·공개 소개)가 현재 규칙으로 다시 거른다.
#: 2026-10-14.4: 설문 안내문·질문·조각 문장을 원문 문장으로 고르지 않는다(`is_not_doctor_statement`).
#: 2026-10-14.5: 원장 목소리 노트(진료 철학·환자 약속·핵심 메시지)만, 약·검사 설명 문장 제외.
AUTOMATIC_ESSENCE_POLICY_VERSION = "2026-10-14.5"

# 원장의 답이 아니라 설문지가 원장에게 하는 말(안내·요청·질문)의 표지. 원장 자신의 문장에는 원장을
# 부르는 말·답변 요청이 없다. 2026-10-14 신기한속 v2가 "모든 질문은 실제 원장님의 말투로, 풍부하게
# 답변 부탁드립니다."를 필수 문구로 골랐다.
_INSTRUCTION_MARKERS = (
    "부탁드립니다", "부탁드려요", "부탁합니다", "답변", "응답", "질문", "문항", "설문",
    "작성해", "작성 해", "기재", "적어 주", "적어주", "기입", "체크해", "선택해 주", "선택해주",
    "입력해 주", "입력해주", "원장님의", "원장님께", "원장님은", "원장님이", "원장님에",
    "원장님을", "원장님,", "말투로", "내부 인력", "최대한 자세히",
)
_QUESTION_ENDING = re.compile(r"(?:까요|나요|습니까|인가요|는지요|을까|할까|될까)$")
# 문장 앞머리가 접속사·연결 어미(앞 문장의 뒷부분)이면 조각이다("중요하며, 음주, …").
_LEADING_CONNECTIVES = frozenset(
    {"그리고", "또한", "또", "및", "그래서", "하지만", "그러나", "그런데", "따라서", "그러므로", "그러면",
     "게다가", "즉", "혹은", "또는"}
)
_LEADING_CLAUSE_TAILS = ("며", "하고", "되고", "이고", "지만", "는데", "으며", "하여", "이라", "라서")
# 서술어로 끝나는 완결 문장의 끝(마침표·따옴표를 뗀 뒤).
_SENTENCE_FINAL = re.compile(r"(?:다|요|죠)$")
_MIN_DOCTOR_STATEMENT_CHARS = 12
# 약·검사 설명(약 계열·처방 권유·괄호 속 영문 약명/검사명) — 원장의 말이 아니라 설명 자료다
# (2026-10-14 세종다온 v6: "트립탄(Triptan) 계열 …", "… 처방하기 좋습니다", "PWV (Pulse Wave …").
_DRUG_OR_EXAM_DESCRIPTION = re.compile(r"계열|처방하|복용이 편리|[(（]\s*[A-Za-z]")
_QUOTE_TAIL = "\"'“”‘’「」『』)]）"


def is_not_doctor_statement(sentence: str) -> bool:
    """필수 문구·공개 소개로 옮길 수 없는 문장인가 — 설문 안내·요청, 질문, 조각.

    원장의 답(설문 답변·홈페이지 문장)만 원문 그대로 옮긴다. 원장을 부르거나 답을 요청하는 문장,
    질문, 접속사·연결 어미로 시작하거나 서술어로 끝나지 않는 조각, 12자 미만 문장은 고르지 않는다.
    """

    text = normalize_sentence(sentence)
    if len(text) < _MIN_DOCTOR_STATEMENT_CHARS:
        return True
    folded = re.sub(r"\s+", " ", text)
    if any(marker in folded for marker in _INSTRUCTION_MARKERS):
        return True
    if _DRUG_OR_EXAM_DESCRIPTION.search(folded):
        return True
    body = folded.rstrip(".!。！…~ ").rstrip(_QUOTE_TAIL).rstrip(".!。！…~ ")
    if folded.endswith(("?", "？")) or _QUESTION_ENDING.search(body):
        return True
    first = re.split(r"[\s,，]+", body, maxsplit=1)[0].strip(_QUOTE_TAIL)
    if first in _LEADING_CONNECTIVES or (
        len(first) >= 2 and first.endswith(_LEADING_CLAUSE_TAILS)
    ):
        return True
    return not _SENTENCE_FINAL.search(body)

#: 원문 문장만 담은 자동 판의 필수 문구 방식(`essence_engine`의 MUST_USE_MODE_*와 같은 값).
VERBATIM_MUST_USE_MODES = frozenset({"SURVEY_VERBATIM", "SOURCE_VERBATIM"})

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


# 띄어쓰기·하이픈·조사 변형을 접은 글(`_folded`)에서 찾는 주장 꼴. "통증이 없습니다"·"부작용 걱정
# 없는"·"한 번으로 끝"·"pain-free"·"zero side effects"·"single session"이 모두 같은 판정을 받는다.
_FOLDED_CLAIM_PATTERNS = (
    re.compile(
        r"(?:통증|아픔|부작용|흉터|회복기간|다운타임|붓기|멍|재발|출혈|절개)"
        r"(?:이|가|은|는|도|의|에|에대한|걱정|부담|염려|걱정이|부담이|위험|위험이)*"
        r"(?:없|제로|zero|0)"
    ),
    re.compile(r"무통|무절개|무흉터|무부작용|노다운타임"),
    re.compile(
        r"(?:1|한|단한|단1)(?:회|번|차례)(?:만에|만으로|으로|로|에|만)?(?:끝|완료|해결|완성|충분|끝나|마무리)"
    ),
    re.compile(
        r"painfree|painless|withoutpain|nopain|zerosideeffect|nosideeffect|sideeffectfree|"
        r"nodowntime|zerodowntime|singlesession|onesession|onevisit|singlevisit|guarantee|"
        r"noscar|scarless|norecovery|cure|eliminat|heal|efficac|effective"
    ),
)


def _folded(text: str) -> str:
    return re.sub(r"[\s\-_·・/]+", "", (text or "").lower())


def claim_words(text: str) -> list[str]:
    """문장에 든 의료 효능·결과·보장·비교 어휘와 수치(`MEDICAL_CLAIM_LEXICON`).

    원문과 함께 띄어쓰기·하이픈을 접은 글에서도 찾는다 — 표기 변형이 판정을 피하지 못하게 한다.
    """

    lowered = (text or "").lower()
    folded = _folded(text)
    found = [
        word for word in MEDICAL_CLAIM_LEXICON if word in lowered or _folded(word) in folded
    ]
    found.extend(match.group(0) for match in _QUANTITY.finditer(text or ""))
    found.extend(match.group(0) for pattern in _FOLDED_CLAIM_PATTERNS for match in pattern.finditer(folded))
    return found


def is_medical_claim(text: str) -> bool:
    return bool(claim_words(text))


def has_critical_terms(*texts: str | None) -> bool:
    """의료 주장 어휘나 안전 어휘가 하나라도 들었는가(필수 문구 지적 면제의 fail-closed 판정)."""

    joined = " ".join(text for text in texts if text).lower()
    folded = _folded(joined)
    return bool(claim_words(joined)) or any(
        term in joined or _folded(term) in folded for term in SAFETY_TERMS
    )


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


def automatic_version_verbatim_texts(philosophy: object) -> set[str] | None:
    """자동 판이면 현재 정책으로 원문 대조를 마친 필수 문구 집합, 레거시 판이면 None.

    옛 정책 판·원문 방식이 아닌 판은 빈 집합이다 — 소비 단계가 그 판의 필수 문구를 쓰지 않는다.
    """

    metadata = getattr(philosophy, "synthesis_metadata", None) or {}
    if not isinstance(metadata, dict) or metadata.get("mode") != "AUTOMATIC":
        return None
    must_use = metadata.get("must_use") if isinstance(metadata.get("must_use"), dict) else {}
    if (
        metadata.get("policy_version") != AUTOMATIC_ESSENCE_POLICY_VERSION
        or must_use.get("mode") not in VERBATIM_MUST_USE_MODES
    ):
        return set()
    return {
        normalize_sentence(item.get("text"))
        for item in must_use.get("items") or []
        if isinstance(item, dict)
        and item.get("text")
        and not is_not_doctor_statement(str(item.get("text")))
    }


__all__ = (
    "AUTOMATIC_ESSENCE_POLICY_VERSION",
    "automatic_version_verbatim_texts",
    "is_not_doctor_statement",
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

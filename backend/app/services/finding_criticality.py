"""지적·차단 사유의 등급 — Critical(사람을 부를 수 있다)인가, 스스로 풀어야 하는가.

2026-10-14 Full-Automatic 설계 §0.1·§1.3(W4)의 한 표다. 생성·발행 게이트·최소 교정·사후 검수가
같은 판정을 쓴다. 이 모듈은 잎이다 — 다른 앱 모듈을 import하지 않는다(재시도 정책이 읽는다).

등급:

- ``CRITICAL`` (2026-10-15 대표 확정 정의 — "의학 논문이 아니다"): 둘뿐이다.
  (1) 환자유인·불법 의료광고 — 완치·효과 장담·결과 보장, 최저가·가격 우위, 할인·이벤트·무료 유인, 유인형
      광고 문구(금지 표현 필터 `medical_filter`의 결정적 차단 + 안전망 어휘 넷).
  (2) 병원으로서 무책임한 허위정보 — 본문이 위험한 행동을 권하거나 당연하게 만들거나 위험한 거짓을 말한다
      ("흉통이 있어도 집에서 쉬면 됩니다", "혈변은 걱정할 필요 없습니다", 틀린 약 용량, 처방약을 끊으라는 말).
  등급을 알 수 없는 지적(판정 null·합성 지적)은 Critical이다(fail closed). 감지 자체가 Critical-blocked는
  아니다 — 먼저 기존 교정·재작성·주제 교체 계단을 탄다.
- ``NONCRITICAL``: 그 밖의 모두 — 어떤 종류든 **누락**(119·응급 안내, 주의·금기·항응고제·조영제 안내, 약한
  응급 권고), 병원 사실 불일치, 보장이 아닌 과장, 사소한 사실 오류(경계 검사 수치 등), 문체·참고자료. 발행을
  막지 않는다 — 진술 문장이면 그 문장을 결정적으로 지우고(W4), 지울 문장이 없으면(누락) 기록만 남기고 진행한다
  (`finding_is_record_only`).
- ``RESPECT``: 사람이 만든 공개 권한 상태(근거 철회·PAUSED·비공개). Critical처럼 막지만 자동으로
  풀지 않고 알리지도 않는다.
- ``SYSTEM``: 시스템 결함(검수 설정 오류·예산 기록 판독 불가). 개발 확인 대상이다.

검수 기록의 판정은 세대가 셋이다. v4(`critical_rule` = `CRITICAL_RULE`)는 검수 모델이 위 정의로 판정했으므로
그 `critical`(+ 안전망 어휘)을 따른다. v3(`critical`이 bool, 규칙 표지 없음)는 옛 넓은 정의(응급 안내 누락·
병원 사실까지 Critical)로 판정됐으므로 돈을 쓰지 않고 결정적으로 다시 매긴다(`_v3_still_critical`). 키가 없는
옛 지적(v2)은 발행 전 경로에서 fail closed이고(v4 검수를 한 번 다시 산다), 공개 글 표시에서는 같은 재등급을
돈 없이 쓴다(`legacy_finding_is_critical`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any


class Criticality(StrEnum):
    CRITICAL = "CRITICAL"
    NONCRITICAL = "NONCRITICAL"
    RESPECT = "RESPECT"
    SYSTEM = "SYSTEM"


# 지적 문구가 응급 안내(119·응급실) 누락을 말하는지 보는 표지 — 최소 교정의 응급 템플릿 판정과 같다.
EMERGENCY_GUIDANCE_MARKERS = ("응급", "119", "구급", "즉시 내원", "즉시 병원")
_BLOCKING_SEVERITIES = frozenset({"HARD", "UNCERTAIN"})

# ── 허용 목록 판정(2026-10-14 Codex 2차, 오케스트레이터 결정 A) ──────────────────────────
# Critical이 **아님**을 적극적으로 보일 때만 비-Critical이다. 나머지는 모두 Critical이다(fail closed).
# 의료·안전 어휘 — 한국어·영어. 지적 문구, 인용, 인용이 가리키는 본문 문장 전체 중 어디에든 있으면
# Critical이다. 대소문자를 무시하고 부분 문자열로 보되, 다른 낱말 속에 들어가는 짧은 어간('약'·'주사'·
# '숨')과 영어 어휘는 낱말 단위로 본다(`_TERM_PATTERNS`).
MEDICAL_SAFETY_LEXICON: tuple[str, ...] = (
    # 응급·긴급 증상
    "응급", "119", "구급", "즉시", "바로 병원", "흉통", "가슴 통증", "호흡", "숨", "의식", "마비",
    "경련", "실신", "출혈", "토혈", "혈변", "흑색변", "검은 변", "고열",
    # 약·용량
    "용량", "용법", "mg", "ml", "mcg", "㎎", "㎖", "복용", "복약", "투여", "투약", "처방", "약", "주사",
    # 금기·위험군
    "금기", "임신", "임산부", "수유", "영유아", "소아", "고령", "기저질환", "알레르기",
    # 시술 위험·합병증
    "부작용", "합병증", "후유증", "위험", "감염", "마취", "쇼크", "사망", "수술", "시술",
    # 진단·효과·질환
    "진단", "치료 효과", "효과", "완치", "재발", "증상", "질환", "통증", "안전",
    # English
    "emergency", "911", "urgent", "chest pain", "breath", "unconscious", "seizure", "bleed",
    "dose", "dosage", "tablet", "pill", "medication", "medicine", "drug", "prescri",
    "contraindicat", "pregnan", "breastfeed", "lactat", "allerg", "side effect", "adverse",
    "complication", "risk", "surgery", "surgical", "procedure", "injection", "inject", "anesthe",
    "infection", "diagnos", "cure", "overdose", "symptom", "safety", "harm", "danger", "fatal",
    "death",
)
# 비-Critical이 될 수 있는 종류 — 의료 내용이 없는 병원 사실·문체·참고자료뿐이다.
NONCRITICAL_KINDS = frozenset({"HOSPITAL_FACT", "STYLE", "REFERENCE"})
# 인용이 있으면 안 되는 필드 — 제목·메타·FAQ(문장 삭제 대상이 아니다).
_NON_BODY_FIELDS = ("title", "meta_description", "faq_question", "faq_answer_summary")
_SENTENCE_END = re.compile(r"[.?!。][)\]\"'”’」』*_~`]*(?=\s|$)")


def _label(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def _field(content: object, name: str) -> str:
    if isinstance(content, Mapping):
        return str(content.get(name) or "")
    return str(getattr(content, name, None) or "")


# 짧아서 다른 낱말 속에 들어가는 어간의 매칭 규칙(낱말 단위). '약'은 '예약·약속·약도·약 2주'에 걸리지
# 않고 '약을·약물·약이·약국·약사·감기약 '처럼 약을 뜻하는 꼴에만 걸린다. '주사'는 '주사위'가, '숨'은
# '숨은·숨기다'가 아니다. 영어 어휘는 앞에 영문자가 붙지 않아야 한다('secure'의 cure, 'img'의 mg).
_TERM_PATTERNS: dict[str, str] = {
    "약": r"(?<![가-힣])약(?:물|을|은|이|의|과|만|으로|에|들|국|제|품|효|사|봉지|(?=\s|$|[.,;:!?)\]'\"”’]))(?!\s*\d)"
    r"|(?:감기|진통|수면|두통|소화|변비|처방|해열|안정)약",
    "주사": r"주사(?!위)",
    "숨": r"숨(?:이|을|쉬|가|차|결|소리)",
}
_LEXICON_RE = re.compile(
    "|".join(
        _TERM_PATTERNS.get(term)
        or (f"(?<![a-z]){re.escape(term)}" if term.isascii() else re.escape(term))
        for term in MEDICAL_SAFETY_LEXICON
    ),
    re.IGNORECASE,
)


def mentions_medical_safety(text: object) -> bool:
    """의료·안전 어휘(`MEDICAL_SAFETY_LEXICON`)가 낱말로 하나라도 있는가."""

    return _LEXICON_RE.search(str(text or "")) is not None


# ── Critical 판정의 안전망 어휘(2026-10-14 대표 결정) ─────────────────────────────────────
# 검수 모델의 판정을 덮는 어휘는 이 넷뿐이다 — 완치·보장·최저가·할인(활용·띄어쓰기 포함)과 영어 대응어.
# 글 자신의 문장(인용·인용 문장)에만 맞춘다 — 검수자의 지적 문구는 아니다(2026-10-15, `_override_hit`).
# 위 `MEDICAL_SAFETY_LEXICON`은 등급 어휘가 아니다. 결정적 삭제의 보호 대상도 아니다 — 삭제는 응급 안내와
# 필수 문구 문장만 지킨다(2026-10-15 대표 결정, 아래 `EMERGENCY_GUIDANCE_TERMS`).
CRITICAL_OVERRIDE_PATTERNS: tuple[str, ...] = (
    r"완\s*치",
    r"보\s*장(?!\s*성)",
    r"최\s*저\s*가",
    r"할\s*인",
    r"(?<![a-z])cure(?:d|s)?(?![a-z])",
    r"(?<![a-z])guarantee",
    r"(?<![a-z])lowest\s+price",
    r"(?<![a-z])discount",
)
_OVERRIDE_RE = re.compile("|".join(CRITICAL_OVERRIDE_PATTERNS), re.IGNORECASE)


def mentions_critical_override(text: object) -> bool:
    """Critical 안전망 어휘(완치·보장·최저가·할인)가 있는가 — 검수 모델이 false라 해도 Critical이다."""

    return _OVERRIDE_RE.search(str(text or "")) is not None


def _quote_spans(text: str, quote: str) -> list[tuple[int, int]]:
    parts = [re.escape(part) for part in quote.split()]
    if not parts:
        return []
    return [(m.start(), m.end()) for m in re.finditer(r"\s+".join(parts), text)]


def _quote_on_structural_line(content: object, quote: str) -> bool:
    """인용이 제목(#)·표(|) 줄에 있는가 — 결정적 삭제가 맡지 않는 줄이다(`content_minimal_correction`)."""

    body = _field(content, "body")
    for start, _end in _quote_spans(body, quote):
        line = body[body.rfind("\n", 0, start) + 1 :].lstrip()
        if line.startswith("#") or line.startswith("|"):
            return True
    return False


def locate_body_sentence(content: object, quote: object) -> str | None:
    """인용이 본문의 **정확히 한 곳**을 가리키면 그 문장 전체. 아니면 None.

    인용이 제목·메타·FAQ에도 있거나, 본문에 없거나 두 곳 이상이면 None이다. 문장은 앞뒤의 종결 부호·
    줄바꿈까지로 넓게 잡는다(넓을수록 어휘 검사가 더 많이 본다).
    """

    quote = str(quote or "").strip()
    if len(quote) < 4 or content is None:
        return None
    if any(_quote_spans(_field(content, name), quote) for name in _NON_BODY_FIELDS):
        return None
    body = _field(content, "body")
    spans = _quote_spans(body, quote)
    if len(spans) != 1:
        return None
    start, end = spans[0]
    line_start = body.rfind("\n", 0, start) + 1
    sentence_start = line_start
    for match in _SENTENCE_END.finditer(body, line_start, start):
        sentence_start = match.end()
    line_end = body.find("\n", end)
    line_end = len(body) if line_end < 0 else line_end
    match = _SENTENCE_END.search(body, max(end - 1, line_start), line_end)
    sentence_end = match.end() if match else line_end
    return body[sentence_start:sentence_end].strip()


def _raw_blocks(finding: Mapping[str, Any]) -> bool:
    """심각도로 발행을 막는 지적인가 — HARD/UNCERTAIN이고 승인 필수 문구를 짚은 것이 아니다."""

    return _label(finding.get("severity")) in _BLOCKING_SEVERITIES and (
        _label(finding.get("target") or "CANDIDATE_TEXT") != "MUST_USE_MESSAGE"
    )


def finding_blocks(finding: Mapping[str, Any], content: object = None) -> bool:
    """발행을 막는 지적인가 — 심각도로 막고, 기록만 남길 비-Critical 지적(`finding_is_record_only`)이 아니다."""

    return _raw_blocks(finding) and not finding_is_record_only(finding, content)


def is_emergency_guidance_finding(finding: Mapping[str, Any]) -> bool:
    """응급 안내(119·응급실)를 말하는 차단 지적인가 — 종류 라벨과 무관하다."""

    return _raw_blocks(finding) and any(
        marker in str(finding.get("message") or "") for marker in EMERGENCY_GUIDANCE_MARKERS
    )


def reviewer_marked_noncritical(finding: Mapping[str, Any]) -> bool:
    """검수 모델이 이 지적에 `critical: false`를 **명시**했는가(v3 검수). 빠졌거나 bool이 아니면 아니다."""

    return finding.get("critical") is False


_POST_TEXT_FIELDS = ("body", *_NON_BODY_FIELDS)


def _post_own_text(finding: Mapping[str, Any], content: object) -> str:
    """글 자신의 문장: 본문을 알면 글에 실제로 있는 인용 + 인용이 가리키는 본문 문장, 모르면 인용만."""

    quote = str(finding.get("quote") or "").strip()
    if not quote:
        return ""
    if content is None:
        return quote
    in_post = any(_quote_spans(_field(content, name), quote) for name in _POST_TEXT_FIELDS)
    sentence = locate_body_sentence(content, quote) or ""
    return f"{quote if in_post else ''} {sentence}"


def _override_hit(finding: Mapping[str, Any], content: object) -> bool:
    """안전망 어휘가 **글 자신의 문장**에 있는가(2026-10-15 대표 결정).

    검수 모델의 지적 문구(`message`)는 보지 않는다 — "결과 보장으로 읽힐 수 있다"처럼 검수자가 쓴 낱말로
    글을 Critical로 만들지 않는다(fca90112). 본문을 알면 인용이 제목·본문·메타·FAQ에 실제로 있을 때만 그
    인용과 인용이 가리키는 본문 문장을 보고, 본문을 모르면 인용(글의 원문을 옮긴 것)만 본다.
    """

    return mentions_critical_override(_post_own_text(finding, content))


# ── 2026-10-15 대표 확정 정의 ─────────────────────────────────────────────────────────
# v4 검수 기록의 지적마다 싣는 판정 규칙 표지. 이 값이 있으면 검수 모델이 확정 정의로 `critical`을 판정했다.
CRITICAL_RULE_KEY = "critical_rule"
CRITICAL_RULE = "owner-2026-10-15"

# 지적 문구가 '무엇이 빠졌다'(누락)를 말하는 표지 — 누락은 지울 문장이 없으므로 기록만 남긴다.
# '근거가 없습니다'(병원 사실 진술 지적)는 누락이 아니다 — 그 문장을 지운다.
_OMISSION_PATTERNS: tuple[str, ...] = (
    r"누락",
    r"빠(?:져|졌|뜨|트)",
    r"(?:안내|언급|설명|경고|주의|단서|정보|문구|권고|당부)\S{0,2}\s*(?:본문\s*)?(?:어디에도\s*)?(?:없|부족)",
    r"다루지\s*않",
    r"추가\s*(?:하세요|해야|가\s*필요|해\s*주세요|하십시오|할\s*필요)",
    r"보강",
    r"덧붙이",
    r"(?:넣|명시하|포함하|알리)(?:세요|어야|야\s*합니다)",
    r"단서가\s*필요",
    r"(?<![a-z])(?:missing|omit|lacks?)(?![a-z])",
)
_OMISSION_RE = re.compile("|".join(_OMISSION_PATTERNS), re.IGNORECASE)

# v3 지적 문구가 '본문이 위험한 행동을 권하거나 위험한 거짓을 말한다'를 묘사하는 표지(결정적 재등급용).
# v3 검수는 옛 넓은 정의(누락·병원 사실까지)로 critical을 매겼다. 다시 사지 않고 이 표지가 있는 의료 안전
# 지적만 Critical로 남긴다 — 누락·보장이 아닌 과장·사소한 사실 오류(경계 수치·추적 주기)는 아니다.
# 본문 문장 자체에도 쓰이는 위험한 권고 꼴 — 지적 문구·인용·인용 문장 어디서든 본다.
_DANGER_IN_TEXT_PATTERNS: tuple[str, ...] = (
    r"걱정\s*(?:할|하실)\s*필요(?:가|는)?\s*없",
    r"걱정\s*(?:하지|안)\s*(?:않아도|해도)",
    r"(?:쉬면|쉬어도|지켜봐도|지켜보면|기다려도|참아도|미뤄도|미루어도|두어도|둬도|방치해도)\s*(?:된|됩|되|돼|괜찮)",
    r"(?:가지|오지|받지|방문하지|내원하지)\s*않아도\s*(?:된|됩|되|돼|괜찮)",
    r"(?:중단|끊)(?:해도|어도|으셔도|하셔도)\s*(?:된|됩|되|돼|괜찮)",
    r"(?:임의로|스스로|마음대로)\s*(?:복용을\s*|약을\s*)?(?:중단|끊)(?:해도|어도|하셔도|으셔도|하세요|으세요|하면|으면)",
)
_DANGER_IN_TEXT_RE = re.compile("|".join(_DANGER_IN_TEXT_PATTERNS), re.IGNORECASE)
_DANGER_STATEMENT_PATTERNS: tuple[str, ...] = (
    *_DANGER_IN_TEXT_PATTERNS,
    r"걱정\s*(?:할|하실)\s*필요",
    r"괜찮다(?:고|는)",
    r"문제\s*(?:가\s*)?없다(?:고|는)",
    r"(?:복용|투약|약|치료|처방)\S*\s*(?:임의로\s*)?(?:중단|끊)(?:하라|해도|하세요|하면|어도|으라|으세요|으면)",
    r"(?:용량|투여량|복용량|용법)\S*.{0,20}?(?:잘못|틀|부정확|초과|과다|과량|위험)",
    r"과량|과다\s*복용",
    r"위험한\s*(?:행동|권고|조언|안내|오정보|정보|거짓|민간요법|자가)",
    r"생명을?\s*위협",
    # 위험 신호를 가볍게 말한다("흑색변을 단순 소화불량으로 안내").
    r"단순(?:한)?\s*\S+?(?:으로|로)\s*(?:만\s*)?(?:안내|설명|단정|표현|치부|소개)",
    r"대수롭지\s*않",
    r"가볍게\s*(?:여기|넘기|봐도|생각해도)",
    r"(?<![a-z])(?:dangerous|overdose|stop\s+taking)(?![a-z])",
)
_DANGER_RE = re.compile("|".join(_DANGER_STATEMENT_PATTERNS), re.IGNORECASE)

# 결정적 삭제가 지키는 응급 안내 표현(2026-10-15 대표 결정). 영어는 대소문자를 가리지 않는다. 이 표현이
# 든 문장은 지우지 않는다 — 비-Critical 지적이 그 문장을 짚었으면 기록만 남기고 진행한다.
EMERGENCY_GUIDANCE_TERMS = (
    "119",
    "응급",
    "응급실",
    "즉시 내원",
    "구급차",
    "emergency",
    "ambulance",
)


def emergency_terms_in(text: object) -> set[str]:
    lowered = str(text or "").lower()
    return {term for term in EMERGENCY_GUIDANCE_TERMS if term in lowered}


def mentions_emergency_guidance(text: object) -> bool:
    """응급 안내 표현(`EMERGENCY_GUIDANCE_TERMS`)이 하나라도 있는가 — 결정적 삭제의 보호 대상."""

    return bool(emergency_terms_in(text))


def is_omission_finding(finding: Mapping[str, Any]) -> bool:
    """지적 문구가 누락(안내·주의·단서가 없다, 추가·보강하라)을 말하는가."""

    return _OMISSION_RE.search(str(finding.get("message") or "")) is not None


# 검수 형식 때문에 붙은 합성 UNCERTAIN(`content_ai_review._SYNTHETIC_UNCERTAIN_MESSAGES`) — 재등급하지 않는다.
_SYNTHETIC_PREFIX = "독립 AI 검수의 "


def _finding_texts(finding: Mapping[str, Any], content: object) -> tuple[str, str]:
    """(지적 문구, 인용 + 인용이 가리키는 본문 문장)."""

    quote = str(finding.get("quote") or "")
    sentence = locate_body_sentence(content, quote) or ""
    return str(finding.get("message") or ""), f"{quote} {sentence}"


# 지적 문구가 '그렇게 오해할 수 있다'(모호·약한 안내)를 말하는 표지 — 본문이 위험한 권고를 **말한** 것이
# 아니므로 지적 문구의 묘사만으로는 Critical이 아니다(대표 정의: 약한 응급 권고는 Critical이 아니다).
_MISREADING_RE = re.compile(r"오해|읽힐\s*수|것처럼\s*읽|처럼\s*보일|여지가|모호")


def describes_dangerous_statement(finding: Mapping[str, Any], content: object = None) -> bool:
    """본문의 위험한 권고·위험한 거짓 진술을 묘사·인용하는가(옛 판정 재등급 표지).

    지적 문구는 묘사 꼴 전부(`_DANGER_STATEMENT_PATTERNS`)를 보되 '오해할 수 있다'처럼 모호함을 말하는 지적은
    빼고, 인용과 인용 문장은 본문에 쓰이는 권고 꼴(`_DANGER_IN_TEXT_PATTERNS` — 쉬면 됩니다·걱정할 필요
    없습니다·끊어도 됩니다 …)만 본다.
    """

    message, text = _finding_texts(finding, content)
    if _DANGER_IN_TEXT_RE.search(text) is not None:
        return True
    return _DANGER_RE.search(message) is not None and _MISREADING_RE.search(message) is None


# 인용·인용 문장에서 보는 유인 꼴은 좁다 — 공개 본문은 이미 금지 표현 필터를 통과했고, '국가검진 무료'·
# '건강보험 혜택' 같은 공적 제도 문장을 유인으로 읽지 않는다. 효과 장담·특가·이벤트처럼 필터 밖의 꼴만 본다.
_INDUCEMENT_IN_TEXT_RE = re.compile(
    r"장\s*담|특\s*가|프로모션|이\s*벤\s*트|최\s*저\s*가|확실히\s*낫|반드시\s*낫", re.IGNORECASE
)


def describes_inducement(finding: Mapping[str, Any], content: object = None) -> bool:
    """글 자신의 문장(인용·인용 문장)에 환자유인·효과 장담 꼴이 있는가(옛 판정 재등급 표지).

    검수자의 지적 문구는 보지 않는다(`_override_hit`과 같은 규칙, 2026-10-15)."""

    return _INDUCEMENT_IN_TEXT_RE.search(_post_own_text(finding, content)) is not None


def _regraded_critical(finding: Mapping[str, Any], content: object) -> bool:
    """확정 정의 이전 판정(v3 `critical: true`·키 없는 옛 지적)을 돈을 쓰지 않고 다시 매긴다.

    유인 광고를 말하면 종류와 무관하게 Critical이다. 병원 사실·문체·참고자료는 비-Critical이고, 의료 안전
    지적은 위험한 권고·거짓 진술을 묘사·인용할 때만(`describes_dangerous_statement`) Critical이다 — 누락·
    보장이 아닌 과장·사소한 사실 오류는 아니다.
    """

    if describes_inducement(finding, content):
        return True
    if _label(finding.get("kind")) in NONCRITICAL_KINDS:
        return False
    return describes_dangerous_statement(finding, content)


def _v3_still_critical(finding: Mapping[str, Any], content: object = None) -> bool:
    """v3(옛 넓은 정의) 판정의 재등급 — 검수 모델이 false라 한 것은 그대로 비-Critical이다."""

    return finding.get("critical") is True and _regraded_critical(finding, content)


def _graded_critical(finding: Mapping[str, Any], content: object) -> bool:
    """차단 심각도의 지적 하나가 확정 정의로 Critical인가(심각도는 보지 않는다)."""

    if _override_hit(finding, content):
        return True
    verdict = finding.get("critical")
    if finding.get(CRITICAL_RULE_KEY) == CRITICAL_RULE:
        # v4: 검수 모델이 확정 정의로 판정했다. null(판정 없음·합성 지적)은 Critical이다.
        return verdict is not False
    if str(finding.get("message") or "").startswith(_SYNTHETIC_PREFIX):
        return True
    if isinstance(verdict, bool):
        return _v3_still_critical(finding, content)
    # 판정 null·키 없는 옛 지적(v2)은 발행 전 경로에서 fail closed다 — 등급 없는 차단은 v4 검수를 한 번 다시
    # 산다(`review_lacks_tier_verdict`). 공개 글 표시의 옛 지적은 `legacy_finding_is_critical`이 돈 없이 다시
    # 매긴다.
    return True


def finding_is_record_only(finding: Mapping[str, Any], content: object = None) -> bool:
    """비-Critical 차단 지적인데 지울 진술 문장이 없는가 — 막지 않고 기록만 남긴다(2026-10-15 대표 결정).

    인용이 없거나 지적이 누락을 말하면 지울 문장이 없다. 본문(`content`)을 알면 인용이 본문의 한 문장을
    가리키지 않거나 그 문장에 응급 안내가 있을 때도(삭제 보호) 지울 수 없다. Critical은 언제나 막는다.
    """

    if not _raw_blocks(finding) or _graded_critical(finding, content):
        return False
    quote = str(finding.get("quote") or "").strip()
    if not quote or is_omission_finding(finding):
        return True
    if content is None:
        return False
    sentence = locate_body_sentence(content, quote)
    return (
        sentence is None
        or mentions_emergency_guidance(sentence)
        or _quote_on_structural_line(content, quote)
    )


def classify_finding(finding: Mapping[str, Any], content: object = None) -> Criticality:
    """독립 검수 지적 하나의 등급(2026-10-15 대표 확정 정의, 모듈 설명).

    발행을 막지 않는 지적(SOFT·필수 문구를 짚은 지적)은 등급을 매기지 않는다(비-Critical). 글 자신의 문장(인용·
    인용이 가리키는 본문 문장 — 지적 문구는 아니다, `_override_hit`)에 안전망 어휘(완치·보장·최저가·할인)가
    있으면 언제나 Critical이다. 나머지는
    판정 세대(`_graded_critical`)가 정한다. 비-Critical이어도 지울 수 없으면 기록만 남는다
    (`finding_is_record_only`).
    """

    if not _raw_blocks(finding):
        return Criticality.NONCRITICAL
    return Criticality.CRITICAL if _graded_critical(finding, content) else Criticality.NONCRITICAL


def blocking_findings(review: object, content: object = None) -> list[Mapping[str, Any]]:
    """저장된 검수 기록(payload)의 차단 지적(기록만 남길 비-Critical 지적은 빼고)."""

    findings = review.get("findings") if isinstance(review, Mapping) else None
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, Mapping) and finding_blocks(f, content)]


def review_still_blocks(review: object, content: object = None) -> bool:
    """저장된 검수 기록에 확정 정의로도 발행을 막는 지적이 남았는가(옛 기록의 `blocking`을 다시 계산)."""

    return bool(blocking_findings(review, content))


def review_lacks_tier_verdict(review: object) -> bool:
    """확신 부족(UNCERTAIN)만 남은 차단에 검수 모델의 `critical` 판정이 **없는** 것이 있는가.

    v3 검수 출력은 모든 지적에 `critical` 키를 싣는다(값이 null이어도). 키가 없는 차단 지적은 등급을
    받은 적이 없어 `classify_finding`이 Critical로 읽는다(2026-10-14 누락 방지 3). 모델이 HARD로 단정한
    지적이 있으면 대상이 아니다 — 그 차단은 교정 계단이 맡고, 교정본은 v3 검수를 다시 받는다.
    """

    blocking = blocking_findings(review)
    if any(_label(finding.get("severity")) == "HARD" for finding in blocking):
        return False
    return any("critical" not in finding for finding in blocking)


def critical_findings(findings: Iterable[object], content: object = None) -> list[Mapping[str, Any]]:
    return [
        finding
        for finding in findings
        if isinstance(finding, Mapping)
        and classify_finding(finding, content) is Criticality.CRITICAL
    ]


def review_has_critical_finding(review: object, content: object = None) -> bool:
    """검수 기록에 Critical 차단 지적이 하나라도 있는가(본문을 모르면 차단 지적은 모두 Critical)."""

    return bool(critical_findings(blocking_findings(review, content), content))


def review_blocks_only_noncritically(review: object, content: object = None) -> bool:
    """차단 지적이 있고 그 전부가 Critical이 아닌가 — 결정적 삭제의 대상이다."""

    blocking = blocking_findings(review, content)
    return bool(blocking) and not critical_findings(blocking, content)


def legacy_finding_is_critical(finding: Mapping[str, Any], content: object = None) -> bool:
    """검수 모델의 `critical` 판정이 **없던** 옛 저장 지적(v3 이전)의 등급(2026-10-14 대표 결정).

    판정받은 적 없는 옛 지적을 모두 Critical로 올리지 않는다. HARD이고 안전망 어휘(완치·보장·최저가·
    할인)가 글 자신의 문장(인용·인용 문장, `_override_hit`)에 있거나, 확정 정의 재등급(`_regraded_critical` — 유인 광고, 위험한
    권고·거짓 진술)이 Critical일 때만 Critical이고, 나머지는 조용히 닫는다. v3 이후의 검수 출력은 언제나
    `critical` 키를 싣는다(값이 없으면 null = Critical).
    """

    if not _raw_blocks(finding):
        return False
    if str(finding.get("message") or "").startswith(_SYNTHETIC_PREFIX):
        return False
    return bool(
        (_label(finding.get("severity")) == "HARD" and _override_hit(finding, content))
        or _regraded_critical(finding, content)
    )


def flag_findings_are_critical(structured_findings: object, content: object = None) -> bool:
    """사후 검수 표시의 구조화 지적에 Critical이 있는가. 구조를 모르면 Critical이다.

    `critical` 키가 있는 지적(v3 검수 출력)은 `classify_finding`, 키가 없는 옛 저장 지적은
    `legacy_finding_is_critical`(어휘 안전망만)로 본다.
    """

    if not isinstance(structured_findings, list) or not structured_findings:
        return True
    for finding in structured_findings:
        if not isinstance(finding, Mapping):
            continue
        if "critical" in finding:
            if classify_finding(finding, content) is Criticality.CRITICAL:
                return True
        elif legacy_finding_is_critical(finding, content):
            return True
    return False


# ── 게이트 차단 코드·생성 거절 라벨 ─────────────────────────────────────────────────
# 발행 게이트(`content_publication.assess_content_publication`)·생성 시도 기록의 코드. 모델 검수
# 차단(`CONTENT_AI_HARD_FINDING`)의 등급은 코드가 아니라 지적이 정한다(`classify_block`).
_CODE_CRITICALITY: Mapping[str, Criticality] = {
    "FORBIDDEN_EXPRESSION": Criticality.CRITICAL,
    "CONTENT_AUTHORITY_CHANGED": Criticality.RESPECT,
    "HOSPITAL_NOT_SERVING": Criticality.RESPECT,
    "CONTENT_AI_REVIEW_CONFIG_ERROR": Criticality.SYSTEM,
    "LEGACY_SPEND_UNKNOWN": Criticality.SYSTEM,
    "MISSING_REFERENCES": Criticality.NONCRITICAL,
    "FAQ_FIELDS_MISSING": Criticality.NONCRITICAL,
    "ESSENCE_NOT_ALIGNED": Criticality.NONCRITICAL,
    "MISSING_APPROVED_ESSENCE": Criticality.NONCRITICAL,
    "CONTENT_AI_REVIEW_STALE": Criticality.NONCRITICAL,
    "CONTENT_AI_REVIEW_UNAVAILABLE": Criticality.NONCRITICAL,
    "COST_BLOCKED": Criticality.NONCRITICAL,
    "CONTENT_NOT_GENERATED": Criticality.NONCRITICAL,
    "GENERATION_REJECTED": Criticality.NONCRITICAL,
    "GENERATION_BUDGET_EXHAUSTED": Criticality.NONCRITICAL,
    "GENERATION_FAILED": Criticality.NONCRITICAL,
    "PROVIDER_TIMEOUT": Criticality.NONCRITICAL,
    "PROVIDER_UNAVAILABLE": Criticality.NONCRITICAL,
    "CONTENT_IMAGE_NOT_READY": Criticality.NONCRITICAL,
    "CONTENT_IMAGE_NOT_VERIFIED": Criticality.NONCRITICAL,
    "IMAGE_GENERATION_FAILED": Criticality.NONCRITICAL,
    "IMAGE_GENERATION_RETRIES_EXHAUSTED": Criticality.NONCRITICAL,
    "CONTENT_IMAGE_POLICY_REJECTED": Criticality.NONCRITICAL,
}

# 생성 거절 라벨(`generation_retry_policy.GENERATION_REJECTION_KINDS`). 금지 표현만 Critical이다 —
# 본문 문장이면 결정적 삭제가 먼저 고친다.
_REJECTION_CRITICALITY: Mapping[str, Criticality] = {
    "forbidden": Criticality.CRITICAL,
    "truncated": Criticality.NONCRITICAL,
    "price": Criticality.NONCRITICAL,
    "references": Criticality.NONCRITICAL,
    "geo": Criticality.NONCRITICAL,
    "seo": Criticality.NONCRITICAL,
    "faq": Criticality.NONCRITICAL,
    "must_use": Criticality.NONCRITICAL,
    "exam_title": Criticality.NONCRITICAL,
    "other": Criticality.NONCRITICAL,
}


def classify_block(code: object, *, review: object = None, content: object = None) -> Criticality:
    """발행 게이트·생성 시도 기록의 차단 코드 하나의 등급.

    `CONTENT_AI_HARD_FINDING`은 저장된 검수(`review`)의 지적이 정한다. 모르는 코드는 Critical이
    아니다 — 사람을 부르는 사유는 위 표에 이름으로 있어야 한다.
    """

    label = _label(code)
    if label == "CONTENT_AI_HARD_FINDING":
        return (
            Criticality.CRITICAL
            if review_has_critical_finding(review, content)
            else Criticality.NONCRITICAL
        )
    return _CODE_CRITICALITY.get(label, Criticality.NONCRITICAL)


def classify_rejection_kind(kind: object) -> Criticality:
    """생성 거절 라벨 하나의 등급."""

    return _REJECTION_CRITICALITY.get(str(kind or "").strip().lower(), Criticality.NONCRITICAL)

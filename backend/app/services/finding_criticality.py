"""지적·차단 사유의 등급 — Critical(사람을 부를 수 있다)인가, 스스로 풀어야 하는가.

2026-10-14 Full-Automatic 설계 §0.1·§1.3(W4)의 한 표다. 생성·발행 게이트·최소 교정·사후 검수가
같은 판정을 쓴다. 이 모듈은 잎이다 — 다른 앱 모듈을 import하지 않는다(재시도 정책이 읽는다).

등급:

- ``CRITICAL``: 불법 의료광고(금지 표현 필터, 할인·유인 포함), 의료 내용을 다루는 모든 검수 지적
  (응급 안내·용량·금기·시술 위험·긴급 증상·의학적 오정보 — 종류 라벨이 아니라 내용으로 정한다), 등급을
  알 수 없는 지적(fail closed). 감지 자체가 Critical-blocked는 아니다 — 먼저 기존 교정·재작성·주제 교체
  계단을 탄다.
  (출력 주입·tenant 혼입의 결정적 검사는 아직 없다 — 생기면 여기 코드로 더한다.)
- ``NONCRITICAL``: 검수 모델이 `critical: false`로 판정한, 의료 내용이 없는 병원 사실(진료 철학 문장·경력·개원 연도·시설·예약·홍보 문구)·
  문체·참고자료·검수 장애·공급자 장애 등. 지적 문장을 결정적으로 지우고 진행하며(숫자를 바꿔 넣지
  않는다) 교정·주제 교체 예산을 쓰지 않는다.
- ``RESPECT``: 사람이 만든 공개 권한 상태(근거 철회·PAUSED·비공개). Critical처럼 막지만 자동으로
  풀지 않고 알리지도 않는다.
- ``SYSTEM``: 시스템 결함(검수 설정 오류·예산 기록 판독 불가). 개발 확인 대상이다.
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
# 위 `MEDICAL_SAFETY_LEXICON`은 등급 어휘가 아니다. 결정적 삭제의 보호 대상도 아니다 — 삭제는 응급 안내와
# 필수 문구 문장만 지킨다(2026-10-15 대표 결정, `content_minimal_correction.EMERGENCY_GUIDANCE_TERMS`).
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


def finding_blocks(finding: Mapping[str, Any]) -> bool:
    """발행을 막는 지적인가 — HARD/UNCERTAIN이고 승인 필수 문구를 짚은 것이 아니다."""

    return _label(finding.get("severity")) in _BLOCKING_SEVERITIES and (
        _label(finding.get("target") or "CANDIDATE_TEXT") != "MUST_USE_MESSAGE"
    )


def is_emergency_guidance_finding(finding: Mapping[str, Any]) -> bool:
    """응급 안내(119·응급실)를 말하는 차단 지적인가 — 종류 라벨과 무관하다."""

    return finding_blocks(finding) and any(
        marker in str(finding.get("message") or "") for marker in EMERGENCY_GUIDANCE_MARKERS
    )


def reviewer_marked_noncritical(finding: Mapping[str, Any]) -> bool:
    """검수 모델이 이 지적에 `critical: false`를 **명시**했는가(v3 검수). 빠졌거나 bool이 아니면 아니다."""

    return finding.get("critical") is False


def _override_hit(finding: Mapping[str, Any], content: object) -> bool:
    sentence = locate_body_sentence(content, finding.get("quote")) or ""
    return mentions_critical_override(
        f"{finding.get('message') or ''} {finding.get('quote') or ''} {sentence}"
    )


def classify_finding(finding: Mapping[str, Any], content: object = None) -> Criticality:
    """독립 검수 지적 하나의 등급(2026-10-14 대표 결정 — 등급은 검수 모델이 정한다).

    발행을 막는 지적은 검수 모델이 `critical: false`를 명시했고 지적 문구·인용·인용이 가리키는 본문
    문장에 안전망 어휘(완치·보장·최저가·할인, `mentions_critical_override`)가 없을 때만 비-Critical이다.
    `critical`이 true이거나 없으면(null·옛 기록·합성 지적) Critical이다. 발행을 막지 않는 지적(SOFT·필수
    문구를 짚은 지적)은 등급을 매기지 않는다(비-Critical). 비-Critical이어도 결정적으로 지울 수 없으면
    (인용 없음·제목 등) 삭제 계획(`content_minimal_correction.plan_noncritical_fixes`)이 맡지 않는다.
    """

    if not finding_blocks(finding):
        return Criticality.NONCRITICAL
    if not reviewer_marked_noncritical(finding) or _override_hit(finding, content):
        return Criticality.CRITICAL
    return Criticality.NONCRITICAL


def blocking_findings(review: object) -> list[Mapping[str, Any]]:
    """저장된 검수 기록(payload)의 차단 지적."""

    findings = review.get("findings") if isinstance(review, Mapping) else None
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, Mapping) and finding_blocks(f)]


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

    return bool(critical_findings(blocking_findings(review), content))


def review_blocks_only_noncritically(review: object, content: object = None) -> bool:
    """차단 지적이 있고 그 전부가 Critical이 아닌가 — 결정적 삭제의 대상이다."""

    blocking = blocking_findings(review)
    return bool(blocking) and not critical_findings(blocking, content)


def legacy_finding_is_critical(finding: Mapping[str, Any], content: object = None) -> bool:
    """검수 모델의 `critical` 판정이 **없던** 옛 저장 지적(v3 이전)의 등급(2026-10-14 대표 결정).

    판정받은 적 없는 옛 지적을 모두 Critical로 올리지 않는다. HARD이고 안전망 어휘(완치·보장·최저가·
    할인)가 지적 문구·인용·인용 문장에 있을 때만 Critical이고, 나머지는 조용히 닫는다. v3 이후의 검수
    출력은 언제나 `critical` 키를 싣는다(값이 없으면 null = Critical).
    """

    return bool(
        finding_blocks(finding)
        and _label(finding.get("severity")) == "HARD"
        and _override_hit(finding, content)
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

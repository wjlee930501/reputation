"""지적·차단 사유의 등급 — Critical(사람을 부를 수 있다)인가, 스스로 풀어야 하는가.

2026-10-14 Full-Automatic 설계 §0.1·§1.3(W4)의 한 표다. 생성·발행 게이트·최소 교정·사후 검수가
같은 판정을 쓴다. 이 모듈은 잎이다 — 다른 앱 모듈을 import하지 않는다(재시도 정책이 읽는다).

등급:

- ``CRITICAL``: 불법 의료광고(금지 표현 필터, 할인·유인 포함), 의료 내용을 다루는 모든 검수 지적
  (응급 안내·용량·금기·시술 위험·긴급 증상·의학적 오정보 — 종류 라벨이 아니라 내용으로 정한다), 등급을
  알 수 없는 지적(fail closed). 감지 자체가 Critical-blocked는 아니다 — 먼저 기존 교정·재작성·주제 교체
  계단을 탄다.
  (출력 주입·tenant 혼입의 결정적 검사는 아직 없다 — 생기면 여기 코드로 더한다.)
- ``NONCRITICAL``: 의료 내용이 없는 병원 사실(진료 철학 문장·경력·개원 연도·시설·예약·홍보 문구)·
  문체·참고자료·검수 장애·공급자 장애 등. 지적 문장을 결정적으로 지우고 진행하며(숫자를 바꿔 넣지
  않는다) 교정·주제 교체 예산을 쓰지 않는다.
- ``RESPECT``: 사람이 만든 공개 권한 상태(근거 철회·PAUSED·비공개). Critical처럼 막지만 자동으로
  풀지 않고 알리지도 않는다.
- ``SYSTEM``: 시스템 결함(검수 설정 오류·예산 기록 판독 불가). 개발 확인 대상이다.
"""

from __future__ import annotations

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

# ── 내용으로 정하는 등급(2026-10-14 Codex P1-2) ───────────────────────────────────────
# 등급은 지적의 종류 라벨이 아니라 **내용**(지적 문구 + 인용 문장)이 정한다. 아래 의료 위험 표지가
# 하나라도 있으면 종류와 무관하게 Critical이다 — 응급 안내·119, 약의 용량·횟수·복용, 금기·위험군,
# 시술 위험·합병증, 바로 진료가 필요한 증상, 의학적 오정보. 모르면 Critical이다(fail closed).
_MEDICAL_RISK_MARKERS = (
    *EMERGENCY_GUIDANCE_MARKERS,
    "응급실", "구급차",
    # 약의 용량·횟수·복용
    "용량", "용법", "복용", "복약", "투약", "투여", "처방", "약물", "약을", "약은", "약이", "주사",
    "mg", "ml", "mcg", "㎎", "㎖", "밀리그램", "밀리리터", "하루 ", "1일 ", "회분",
    # 금기·위험군
    "금기", "임산부", "임신", "수유", "영유아", "소아", "어린이", "고령", "노인", "기저질환",
    "당뇨 환자", "고혈압 환자", "신장 질환", "간 질환", "알레르기", "과민",
    # 시술 위험·합병증
    "부작용", "합병증", "위험", "출혈", "감염", "마취", "쇼크", "아나필락시스", "사망", "후유증",
    # 바로 진료가 필요한 증상
    "흉통", "가슴 통증", "호흡곤란", "숨이 차", "의식", "마비", "경련", "실신", "토혈", "혈변",
    "흑색변", "검은 변", "고열",
    # 의학적 오정보·효과 단정
    "오정보", "잘못된 의학", "의학적 근거", "의학적으로", "효과", "완치", "부정확한 의학",
    "진단 기준", "오진", "질환의 원인", "치료법",
)
# 의료 내용이 없는 병원 사실임을 **적극적으로** 말하는 표지. 병원 사실(HOSPITAL_FACT) 지적은 이 표지가
# 있고 의료 위험 표지가 없을 때만 Critical이 아니다 — 진료 철학 문장·경력·수련·개원 연도·시설 설명·
# 예약·기간 같은 운영 정보·의료 내용 없는 홍보 문구.
_NONMEDICAL_FACT_MARKERS = (
    "철학", "원칙", "가치", "비전", "슬로건", "약속", "신념", "진료 방침",
    "경력", "수련", "학력", "졸업", "전공의", "전문의 자격", "약력", "이력", "출신",
    "개원", "설립", "연혁", "년째", "주년",
    "시설", "장비", "병상", "규모", "주차", "위치", "층", "인테리어", "공간",
    "예약", "진료 시간", "진료시간", "운영 시간", "휴진", "대기", "기간",
    "홍보", "마케팅", "수상", "인증", "언론", "소개",
)
# 의료 내용이 없을 때 Critical이 아닌 종류(문체·참고자료). 병원 사실은 위 표지로 따로 본다.
_NONMEDICAL_KINDS = frozenset({"STYLE", "REFERENCE"})


def _label(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def _finding_text(finding: Mapping[str, Any]) -> str:
    return f"{finding.get('message') or ''} {finding.get('quote') or ''}"


def _mentions(text: str, markers: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def finding_blocks(finding: Mapping[str, Any]) -> bool:
    """발행을 막는 지적인가 — HARD/UNCERTAIN이고 승인 필수 문구를 짚은 것이 아니다."""

    return _label(finding.get("severity")) in _BLOCKING_SEVERITIES and (
        _label(finding.get("target") or "CANDIDATE_TEXT") != "MUST_USE_MESSAGE"
    )


def is_emergency_guidance_finding(finding: Mapping[str, Any]) -> bool:
    """응급 안내(119·응급실)를 말하는 차단 지적인가 — 종류 라벨과 무관하다."""

    if not finding_blocks(finding):
        return False
    return _mentions(str(finding.get("message") or ""), EMERGENCY_GUIDANCE_MARKERS)


def classify_finding(finding: Mapping[str, Any]) -> Criticality:
    """독립 검수 지적 하나의 등급 — 내용이 정하고, 모르면 Critical이다(fail closed).

    1. 발행을 막지 않는 지적(SOFT·필수 문구를 짚은 지적)은 Critical이 아니다.
    2. 지적 문구나 인용 문장에 의료 위험 표지(응급·용량·금기·위험·긴급 증상·의학적 오정보)가 있으면
       종류와 무관하게 Critical이다.
    3. 의료 안전(MEDICAL_SAFETY)·종류를 읽을 수 없는 지적은 Critical이다.
    4. 병원 사실(HOSPITAL_FACT)은 의료 내용이 없는 병원 사실(철학·경력·개원·시설·예약·홍보)임을
       말하는 표지가 있을 때만 Critical이 아니다. 아니면 Critical이다.
    5. 의료 내용이 없는 문체·참고자료 지적은 Critical이 아니다.
    """

    if not finding_blocks(finding):
        return Criticality.NONCRITICAL
    text = _finding_text(finding)
    if _mentions(text, _MEDICAL_RISK_MARKERS):
        return Criticality.CRITICAL
    kind = _label(finding.get("kind"))
    if kind in _NONMEDICAL_KINDS:
        return Criticality.NONCRITICAL
    if kind == "HOSPITAL_FACT" and _mentions(text, _NONMEDICAL_FACT_MARKERS):
        return Criticality.NONCRITICAL
    return Criticality.CRITICAL


def blocking_findings(review: object) -> list[Mapping[str, Any]]:
    """저장된 검수 기록(payload)의 차단 지적."""

    findings = review.get("findings") if isinstance(review, Mapping) else None
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, Mapping) and finding_blocks(f)]


def critical_findings(findings: Iterable[object]) -> list[Mapping[str, Any]]:
    return [
        finding
        for finding in findings
        if isinstance(finding, Mapping) and classify_finding(finding) is Criticality.CRITICAL
    ]


def review_has_critical_finding(review: object) -> bool:
    """검수 기록에 Critical 차단 지적이 하나라도 있는가."""

    return bool(critical_findings(blocking_findings(review)))


def review_blocks_only_noncritically(review: object) -> bool:
    """차단 지적이 있고 그 전부가 Critical이 아닌가 — 결정적 해소의 대상이다."""

    blocking = blocking_findings(review)
    return bool(blocking) and not critical_findings(blocking)


def flag_findings_are_critical(structured_findings: object) -> bool:
    """사후 검수 표시의 구조화 지적에 Critical이 있는가.

    구조를 모르면(옛 표시·빈 목록) Critical로 본다 — 의료 안전을 모르는 채로 조용히 닫지 않는다.
    """

    if not isinstance(structured_findings, list) or not structured_findings:
        return True
    return bool(critical_findings(structured_findings))


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


def classify_block(code: object, *, review: object = None) -> Criticality:
    """발행 게이트·생성 시도 기록의 차단 코드 하나의 등급.

    `CONTENT_AI_HARD_FINDING`은 저장된 검수(`review`)의 지적이 정한다. 모르는 코드는 Critical이
    아니다 — 사람을 부르는 사유는 위 표에 이름으로 있어야 한다.
    """

    label = _label(code)
    if label == "CONTENT_AI_HARD_FINDING":
        return (
            Criticality.CRITICAL if review_has_critical_finding(review) else Criticality.NONCRITICAL
        )
    return _CODE_CRITICALITY.get(label, Criticality.NONCRITICAL)


def classify_rejection_kind(kind: object) -> Criticality:
    """생성 거절 라벨 하나의 등급."""

    return _REJECTION_CRITICALITY.get(str(kind or "").strip().lower(), Criticality.NONCRITICAL)

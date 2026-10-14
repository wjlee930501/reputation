"""지적·차단 사유의 등급 — Critical(사람을 부를 수 있다)인가, 스스로 풀어야 하는가.

2026-10-14 Full-Automatic 설계 §0.1·§1.3(W4)의 한 표다. 생성·발행 게이트·최소 교정·사후 검수가
같은 판정을 쓴다. 이 모듈은 잎이다 — 다른 앱 모듈을 import하지 않는다(재시도 정책이 읽는다).

등급:

- ``CRITICAL``: 불법 의료광고(금지 표현 필터, 할인·유인 포함), 의료 안전 HARD(응급 안내 누락·위험한
  의학 오정보). 감지 자체가 Critical-blocked는 아니다 — 먼저 기존 교정·재작성·주제 교체 계단을 탄다.
  (출력 주입·tenant 혼입의 결정적 검사는 아직 없다 — 생기면 여기 코드로 더한다.)
- ``NONCRITICAL``: 근거 없는 병원 주장(HOSPITAL_FACT)·승인 자료와 다른 수치·확신 부족(UNCERTAIN)·
  문체·참고자료·검수 장애·공급자 장애 등. 결정적으로 고치고(문장 삭제·승인 수치 치환) 진행하며
  교정·주제 교체 예산을 쓰지 않는다.
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
# HARD여도 Critical이 아닌 지적 종류. 근거 없는 병원 주장·수치 불일치(HOSPITAL_FACT)와 문체·참고자료.
_NONCRITICAL_HARD_KINDS = frozenset({"HOSPITAL_FACT", "STYLE", "REFERENCE"})


def _label(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def finding_blocks(finding: Mapping[str, Any]) -> bool:
    """발행을 막는 지적인가 — HARD/UNCERTAIN이고 승인 필수 문구를 짚은 것이 아니다."""

    return _label(finding.get("severity")) in _BLOCKING_SEVERITIES and (
        _label(finding.get("target") or "CANDIDATE_TEXT") != "MUST_USE_MESSAGE"
    )


def is_emergency_guidance_finding(finding: Mapping[str, Any]) -> bool:
    """응급 안내(119·응급실)가 빠졌다는 차단 의료 안전 지적인가."""

    if _label(finding.get("kind")) != "MEDICAL_SAFETY" or not finding_blocks(finding):
        return False
    message = str(finding.get("message") or "")
    return any(marker in message for marker in EMERGENCY_GUIDANCE_MARKERS)


def classify_finding(finding: Mapping[str, Any]) -> Criticality:
    """독립 검수 지적 하나의 등급.

    의료 안전 HARD와 응급 안내 누락(HARD·UNCERTAIN)만 Critical이다. 병원 사실(HOSPITAL_FACT)은
    HARD여도 근거 없는 병원 주장·수치 불일치라 Critical이 아니다 — 지우거나 승인 수치로 바꾸면 된다.
    확신 부족(UNCERTAIN) 의료 안전, SOFT·문체·참고자료, 필수 문구를 짚은 지적도 Critical이 아니다.
    """

    if not finding_blocks(finding):
        return Criticality.NONCRITICAL
    if is_emergency_guidance_finding(finding):
        return Criticality.CRITICAL
    if (
        _label(finding.get("severity")) == "HARD"
        and _label(finding.get("kind")) not in _NONCRITICAL_HARD_KINDS
    ):
        # 의료 안전 HARD — 종류를 읽을 수 없는 HARD도 안전한 쪽(Critical)으로 본다.
        return Criticality.CRITICAL
    return Criticality.NONCRITICAL


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

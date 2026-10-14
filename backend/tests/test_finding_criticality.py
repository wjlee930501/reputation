"""지적·차단 사유 등급표(W4, Full-Automatic 설계 §1.3) — 한 모듈이 정하고 모두가 같은 판정을 쓴다."""

from __future__ import annotations

import pytest

from app.services.finding_criticality import (
    Criticality,
    classify_block,
    classify_finding,
    classify_rejection_kind,
    flag_findings_are_critical,
    review_blocks_only_noncritically,
    review_has_critical_finding,
)
from app.workers.generation_retry_policy import (
    GENERATION_REJECTION_KINDS,
    GenerationRetryClass,
    has_model_declared_hard_finding,
    retry_class_for,
)

C, N = Criticality.CRITICAL, Criticality.NONCRITICAL


def _finding(severity: str, kind: str, message: str = "지적", **extra) -> dict:
    return {"severity": severity, "kind": kind, "message": message, **extra}


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        # 의료 안전 HARD(위험한 의학 오정보)는 Critical이다.
        (_finding("HARD", "MEDICAL_SAFETY", "흑색변을 단순 소화불량으로 안내했습니다", quote="q"), C),
        (_finding("HARD", "MEDICAL_SAFETY", "근거 없는 효과 단정"), C),
        # 종류를 읽을 수 없는 HARD는 안전한 쪽(Critical)이다.
        ({"severity": "HARD", "message": "x"}, C),
        # 응급 안내 누락은 UNCERTAIN이어도 Critical이다(검은 변 응급 사례).
        (_finding("UNCERTAIN", "MEDICAL_SAFETY", "검은 변이 나오면 119·응급실 안내가 없습니다"), C),
        (_finding("HARD", "MEDICAL_SAFETY", "응급 신호를 알려야 합니다"), C),
        # 감사 실례: 진료 철학 문장(근거 없는 병원 주장) — 의료 내용 없는 병원 사실, Critical이 아니다.
        (_finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 철학", quote="환자 한 분 한 분"), N),
        (_finding("HARD", "HOSPITAL_FACT", "원장 수련기관이 프로필과 다릅니다", quote="q"), N),
        (_finding("HARD", "HOSPITAL_FACT", "개원 연도가 승인 자료와 다릅니다", quote="개원 20년"), N),
        (_finding("UNCERTAIN", "HOSPITAL_FACT", "확인할 수 없는 장비 보유"), N),
        (_finding("HARD", "HOSPITAL_FACT", "회복 기간이 승인 자료와 다릅니다", quote="8주"), N),
        # Codex P1-2: 종류가 HOSPITAL_FACT여도 내용이 의료 위험이면 Critical이다.
        (_finding("HARD", "HOSPITAL_FACT", "119 응급 안내가 승인 자료와 다릅니다"), C),
        (_finding("HARD", "HOSPITAL_FACT", "투약량이 다릅니다", quote="하루 5mg을 드세요"), C),
        (_finding("HARD", "HOSPITAL_FACT", "임산부도 시술받을 수 있다고 안내", quote="q"), C),
        (_finding("HARD", "HOSPITAL_FACT", "시술 합병증 설명이 없습니다", quote="q"), C),
        (_finding("UNCERTAIN", "HOSPITAL_FACT", "흉통이 있어도 기다려도 된다고 안내", quote="q"), C),
        # 의료 내용이 없다고 말하는 표지가 없는 병원 사실은 모른다 — Critical(fail closed).
        (_finding("HARD", "HOSPITAL_FACT", "승인 자료에서 확인할 수 없습니다", quote="q"), C),
        (_finding("HARD", "HOSPITAL_FACT", "승인 자료에서 심장 초음파를 확인할 수 없습니다"), C),
        # 의료 안전은 확신 부족이어도 Critical이다(fail closed).
        (_finding("UNCERTAIN", "MEDICAL_SAFETY", "통계 출처가 불분명합니다", quote="q"), C),
        # 문체·참고자료라도 의료 위험 내용이면 Critical이다.
        (_finding("HARD", "STYLE", "복용 횟수 표현이 모호합니다"), C),
        (_finding("SOFT", "STYLE", "문장이 깁니다"), N),
        (_finding("SOFT", "REFERENCE", "참고자료 주제 불일치"), N),
        # 승인 필수 문구를 짚은 지적은 발행을 막지 않는다 — HARD 의료 안전이어도 Critical이 아니다.
        (_finding("HARD", "MEDICAL_SAFETY", "x", target="MUST_USE_MESSAGE"), N),
    ],
)
def test_finding_classification_table(finding, expected) -> None:
    assert classify_finding(finding) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("FORBIDDEN_EXPRESSION", C),
        ("CONTENT_AUTHORITY_CHANGED", Criticality.RESPECT),
        ("HOSPITAL_NOT_SERVING", Criticality.RESPECT),
        ("CONTENT_AI_REVIEW_CONFIG_ERROR", Criticality.SYSTEM),
        ("LEGACY_SPEND_UNKNOWN", Criticality.SYSTEM),
        ("MISSING_REFERENCES", N),
        ("FAQ_FIELDS_MISSING", N),
        ("ESSENCE_NOT_ALIGNED", N),
        ("MISSING_APPROVED_ESSENCE", N),
        ("CONTENT_AI_REVIEW_STALE", N),
        ("CONTENT_AI_REVIEW_UNAVAILABLE", N),
        ("COST_BLOCKED", N),
        ("CONTENT_NOT_GENERATED", N),
        ("GENERATION_REJECTED", N),
        ("GENERATION_BUDGET_EXHAUSTED", N),
        ("PROVIDER_UNAVAILABLE", N),
        ("CONTENT_IMAGE_NOT_READY", N),
        ("SOMETHING_NEW", N),
    ],
)
def test_block_code_table(code, expected) -> None:
    assert classify_block(code) is expected


def test_model_hard_finding_block_takes_the_grade_of_its_findings() -> None:
    critical = {"findings": [_finding("HARD", "MEDICAL_SAFETY", "위험한 오정보", quote="q")]}
    fact = {"findings": [_finding("HARD", "HOSPITAL_FACT", "근거 없는 진료 철학", quote="q")]}

    assert classify_block("CONTENT_AI_HARD_FINDING", review=critical) is C
    assert classify_block("CONTENT_AI_HARD_FINDING", review=fact) is N
    assert review_has_critical_finding(critical)
    assert not review_has_critical_finding(fact)
    assert review_blocks_only_noncritically(fact)
    assert not review_blocks_only_noncritically(critical)
    # 차단 지적이 없으면 "비-Critical만으로 막힘"도 아니다.
    assert not review_blocks_only_noncritically({"findings": [_finding("SOFT", "STYLE")]})


def test_every_generation_rejection_kind_is_graded() -> None:
    kinds = [kind for kind, _markers in GENERATION_REJECTION_KINDS] + ["other"]
    graded = {kind: classify_rejection_kind(kind) for kind in kinds}

    assert graded.pop("forbidden") is C
    assert set(graded.values()) == {N}


def test_post_publish_flag_without_structure_is_treated_as_critical() -> None:
    assert flag_findings_are_critical(None)
    assert flag_findings_are_critical([])
    assert not flag_findings_are_critical(
        [_finding("HARD", "HOSPITAL_FACT", "진료 철학 문장", quote="q")]
    )
    assert flag_findings_are_critical([_finding("HARD", "MEDICAL_SAFETY", quote="q")])


def test_only_critical_hard_is_the_model_declared_terminal() -> None:
    """병원 사실 HARD는 교정·주제 교체 계단의 종착이 아니다 — 표본 실패처럼 다시 쓴다(W4)."""

    fact = {"findings": [_finding("HARD", "HOSPITAL_FACT", "경력 연수가 다릅니다", quote="q")]}
    safety = {"findings": [_finding("HARD", "MEDICAL_SAFETY", quote="q")]}

    assert not has_model_declared_hard_finding(fact)
    assert has_model_declared_hard_finding(safety)
    assert (
        retry_class_for(
            "CONTENT_AI_HARD_FINDING", model_declared_hard=has_model_declared_hard_finding(fact)
        )
        is GenerationRetryClass.SAMPLE_RECOVERABLE
    )
    assert (
        retry_class_for(
            "CONTENT_AI_HARD_FINDING", model_declared_hard=has_model_declared_hard_finding(safety)
        )
        is GenerationRetryClass.INPUT_CHANGE_REQUIRED
    )

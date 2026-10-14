"""지적·차단 사유 등급표(W4, Full-Automatic 설계 §1.3) — 한 모듈이 정하고 모두가 같은 판정을 쓴다."""

from __future__ import annotations

import pytest

from app.services.finding_criticality import (
    MEDICAL_SAFETY_LEXICON,
    Criticality,
    classify_block,
    classify_finding,
    classify_rejection_kind,
    flag_findings_are_critical,
    mentions_medical_safety,
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


PHILOSOPHY = "본원은 환자 한 분 한 분의 이야기를 끝까지 듣는 것을 진료 원칙으로 삼습니다."
TRAINING = "이현진 원장은 서울성모병원에서 정형외과 전공의 수련을 마쳤습니다."
OPENING = "본원은 개원 15년째 같은 자리를 지키고 있습니다."
CHEST = "본원은 환자의 이야기를 듣는 것을 원칙으로 하며, 흉통에는 즉시 119에 연락하셔야 합니다."
CONTENT = {
    "title": "허리 통증, 언제 병원에 가야 할까요?",
    "body": f"## 안내\n{PHILOSOPHY} {TRAINING}\n{OPENING}\n{CHEST}\n같은 문장입니다. 같은 문장입니다.",
    "meta_description": "허리 통증 내원 시점 안내",
    "faq_question": None,
    "faq_answer_summary": None,
}


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        # ── 허용 목록: 병원 사실·문체·참고자료 + 본문 한 문장 인용 + 의료 어휘 없음 ──
        (_finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙", quote=PHILOSOPHY), N),
        (_finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다", quote="서울성모병원에서 정형외과"), N),
        (_finding("HARD", "HOSPITAL_FACT", "개원 연도가 다릅니다", quote=OPENING), N),
        (_finding("UNCERTAIN", "STYLE", "확신 부족", quote=OPENING), N),
        # ── 그 밖은 모두 Critical(fail closed) ──
        (_finding("HARD", "MEDICAL_SAFETY", "흑색변을 단순 소화불량으로 안내", quote=PHILOSOPHY), C),
        (_finding("UNCERTAIN", "MEDICAL_SAFETY", "확신 부족", quote=PHILOSOPHY), C),
        ({"severity": "HARD", "message": "x", "quote": PHILOSOPHY}, C),
        # Codex 2차 재현: 영어·혼합 언어로 쓴 의료 지적.
        (_finding("HARD", "STYLE", "Missing emergency care instructions for chest pain", quote=OPENING), C),
        (_finding("HARD", "HOSPITAL_FACT", "진료 원칙: contraindication for pregnancy is omitted",
                  quote=PHILOSOPHY), C),
        (_finding("HARD", "HOSPITAL_FACT", "Wrong dose: take 5 mg twice daily", quote=OPENING), C),
        # 인용 없음·제목 인용·본문 두 곳·본문에 없음.
        (_finding("HARD", "HOSPITAL_FACT", "진료 원칙 mismatch", quote=""), C),
        (_finding("HARD", "HOSPITAL_FACT", "제목의 병원 표현", quote="언제 병원에 가야"), C),
        (_finding("HARD", "HOSPITAL_FACT", "반복 문장", quote="같은 문장입니다."), C),
        (_finding("HARD", "HOSPITAL_FACT", "없는 문장", quote="본문에 없는 인용 문장입니다"), C),
        # Codex 2차 재현: 부분 인용이 가리키는 문장 전체에 응급 안내가 있다.
        (_finding("HARD", "HOSPITAL_FACT", "진료 원칙", quote="환자의 이야기를 듣는 것을 원칙으로"), C),
        # 발행을 막지 않는 지적은 등급 대상이 아니다.
        (_finding("SOFT", "STYLE", "문장이 깁니다"), N),
        (_finding("HARD", "MEDICAL_SAFETY", "x", target="MUST_USE_MESSAGE"), N),
    ],
)
def test_finding_classification_allowlist(finding, expected) -> None:
    assert classify_finding(finding, CONTENT) is expected


def test_without_the_body_nothing_is_noncritical() -> None:
    finding = _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙", quote=PHILOSOPHY)

    assert classify_finding(finding) is C
    assert classify_finding(finding, CONTENT) is N


@pytest.mark.parametrize(
    "term",
    ["응급", "119", "즉시", "흉통", "호흡", "의식", "출혈", "용량", "mg", "ml", "복용", "투여", "금기",
     "임신", "수유", "부작용", "합병증", "위험", "수술", "시술", "주사", "약", "진단", "치료 효과",
     "완치", "emergency", "dose", "contraindication", "pregnancy", "side effect", "complication",
     "risk", "surgery", "injection", "medication"],
)
def test_lexicon_holds_the_required_terms(term) -> None:
    assert mentions_medical_safety(f"문장 {term.upper()} 문장")
    assert any(entry.lower() in term.lower() for entry in MEDICAL_SAFETY_LEXICON)


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
    critical = {"findings": [_finding("HARD", "MEDICAL_SAFETY", "위험한 오정보", quote=PHILOSOPHY)]}
    fact = {"findings": [_finding("HARD", "HOSPITAL_FACT", "근거 없는 진료 원칙", quote=PHILOSOPHY)]}

    assert classify_block("CONTENT_AI_HARD_FINDING", review=critical, content=CONTENT) is C
    assert classify_block("CONTENT_AI_HARD_FINDING", review=fact, content=CONTENT) is N
    assert review_has_critical_finding(critical, CONTENT)
    assert not review_has_critical_finding(fact, CONTENT)
    assert review_has_critical_finding(fact)  # 본문을 모르면 Critical이다
    assert review_blocks_only_noncritically(fact, CONTENT)
    assert not review_blocks_only_noncritically(critical, CONTENT)
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
        [_finding("HARD", "HOSPITAL_FACT", "진료 원칙 문장", quote=PHILOSOPHY)], CONTENT
    )
    assert flag_findings_are_critical([_finding("HARD", "MEDICAL_SAFETY", quote=PHILOSOPHY)], CONTENT)


def test_only_critical_hard_is_the_model_declared_terminal() -> None:
    """의료 내용 없는 병원 사실 HARD는 교정·주제 교체 계단의 종착이 아니다 — 표본처럼 다시 쓴다(W4)."""

    fact = {"findings": [_finding("HARD", "HOSPITAL_FACT", "수련기관이 다릅니다", quote=TRAINING)]}
    safety = {"findings": [_finding("HARD", "MEDICAL_SAFETY", quote=TRAINING)]}

    assert not has_model_declared_hard_finding(fact, CONTENT)
    assert has_model_declared_hard_finding(fact)  # 본문을 모르면 fail closed
    assert has_model_declared_hard_finding(safety, CONTENT)
    assert (
        retry_class_for(
            "CONTENT_AI_HARD_FINDING",
            model_declared_hard=has_model_declared_hard_finding(fact, CONTENT),
        )
        is GenerationRetryClass.SAMPLE_RECOVERABLE
    )
    assert (
        retry_class_for(
            "CONTENT_AI_HARD_FINDING", model_declared_hard=has_model_declared_hard_finding(safety)
        )
        is GenerationRetryClass.INPUT_CHANGE_REQUIRED
    )


@pytest.mark.parametrize(
    "text",
    ["예약", "약속", "약도", "예약 가능", "진료 예약은 전화로 받습니다", "약 2주 걸립니다", "주사위",
     "숨은 그림", "secure 결제", "img 태그"],
)
def test_short_stems_do_not_match_inside_unrelated_words(text) -> None:
    assert not mentions_medical_safety(text)


@pytest.mark.parametrize(
    "text",
    ["약을 드세요", "약물", "처방약", "약이 듣지 않으면", "감기약 복용", "예방주사", "숨이 차면",
     "take 5 mg", "Cure rate", "고위험군", "뇌출혈"],
)
def test_medical_forms_still_match(text) -> None:
    assert mentions_medical_safety(text)


def test_typical_philosophy_fact_on_a_plain_sentence_is_noncritical() -> None:
    """운영 표본: '병원 철학 문장의 근거가 없음' 지적이 의료 어휘 없는 문장(예약 안내가 섞여도)을 짚으면
    비-Critical이다 — 결정적 삭제·조용한 종결 대상."""

    sentence = "본원은 예약 시간을 지키고 환자의 이야기를 끝까지 듣는 것을 진료 철학으로 삼습니다."
    content = {"title": "허리 통증 안내", "body": f"## 안내\n{sentence}\n증상이 있으면 진료를 받으세요."}
    finding = _finding("HARD", "HOSPITAL_FACT", "병원 철학 문장의 근거가 없음", quote=sentence)

    assert classify_finding(finding, content) is N

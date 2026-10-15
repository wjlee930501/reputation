"""지적·차단 사유 등급표(W4, Full-Automatic 설계 §1.3) — 한 모듈이 정하고 모두가 같은 판정을 쓴다."""

from __future__ import annotations

import pytest

from app.services.finding_criticality import (
    CRITICAL_RULE,
    CRITICAL_RULE_KEY,
    MEDICAL_SAFETY_LEXICON,
    Criticality,
    classify_block,
    classify_finding,
    classify_rejection_kind,
    flag_findings_are_critical,
    mentions_critical_override,
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
    # 검수 모델이 확정 정의(v4, `critical_rule`)로 `critical: false`를 판정한 지적이 기본이다 — 필드 없음·
    # true·옛 v3 판정(규칙 표지 없음)은 따로 본다.
    return {
        "severity": severity,
        "kind": kind,
        "message": message,
        "critical": False,
        CRITICAL_RULE_KEY: CRITICAL_RULE,
        **extra,
    }


PHILOSOPHY = "본원은 환자 한 분 한 분의 이야기를 끝까지 듣는 것을 진료 원칙으로 삼습니다."
TRAINING = "이현진 원장은 서울성모병원에서 정형외과 전공의 수련을 마쳤습니다."
OPENING = "본원은 개원 15년째 같은 자리를 지키고 있습니다."
CHEST = "본원은 환자의 이야기를 듣는 것을 원칙으로 하며, 흉통에는 즉시 119에 연락하셔야 합니다."
GUARANTEE = "본원은 정확한 진단부터 완치까지 보장합니다."
CONTENT = {
    "title": "허리 통증, 완치까지 함께하는 내원 시점",
    "body": f"## 안내\n{PHILOSOPHY} {TRAINING}\n{OPENING}\n{CHEST}\n같은 문장입니다. 같은 문장입니다.\n"
    f"첫 진료는 할인해 드립니다.\n{GUARANTEE}\n최저 가로 모십니다.\n"
    "Guaranteed results at the lowest price. It cured the patient.",
    "meta_description": "허리 통증 내원 시점 안내",
    "faq_question": None,
    "faq_answer_summary": None,
}


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        # ── 2026-10-14 대표 결정: 등급은 검수 모델이 정한다(`critical`) ──
        (_finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙", quote=PHILOSOPHY), N),
        (_finding("HARD", "MEDICAL_SAFETY", "흑색변 설명이 부족합니다", quote=PHILOSOPHY), N),
        (_finding("UNCERTAIN", "STYLE", "확신 부족", quote=OPENING), N),
        (_finding("HARD", "HOSPITAL_FACT", "진료 원칙 mismatch", quote=""), N),
        (_finding("HARD", "MEDICAL_SAFETY", "흑색변을 단순 소화불량으로 안내", quote=PHILOSOPHY,
                  critical=True), C),
        (_finding("HARD", "HOSPITAL_FACT", "x", quote=PHILOSOPHY, critical=None), C),
        ({"severity": "HARD", "kind": "HOSPITAL_FACT", "message": "x", "quote": PHILOSOPHY}, C),
        # ── 안전망: 글 자신의 문장의 완치·보장·최저가·할인(활용·띄어쓰기·영어)은 false여도 Critical ──
        (_finding("HARD", "HOSPITAL_FACT", "근거 없음", quote=GUARANTEE), C),
        (_finding("HARD", "HOSPITAL_FACT", "근거 없음", quote="최저 가로 모십니다"), C),
        (_finding("HARD", "STYLE", "x", quote="Guaranteed results at the lowest price"), C),
        (_finding("HARD", "STYLE", "x", quote="It cured the patient"), C),
        (_finding("HARD", "STYLE", "제목 문구", quote="완치까지 함께"), C),  # 제목의 인용
        # 인용이 가리키는 본문 문장 전체도 본다.
        (_finding("HARD", "HOSPITAL_FACT", "진료 원칙", quote="첫 진료는"), C),
        # ── 검수자의 지적 문구는 보지 않는다(2026-10-15 — fca90112) ──
        (_finding("HARD", "HOSPITAL_FACT", "완치됩니다라는 표현", quote=OPENING), N),
        (_finding("HARD", "HOSPITAL_FACT", "결과 보장으로 읽힐 수 있습니다", quote=OPENING), N),
        (_finding("HARD", "STYLE", "할인해 드린다는 문장", quote=OPENING), N),
        (_finding("HARD", "STYLE", "Guaranteed results, lowest price", quote=OPENING), N),
        # 글에 없는 인용(검수자가 지어낸 말)도 글의 문장이 아니다.
        (_finding("HARD", "HOSPITAL_FACT", "근거 없음", quote="효과를 보장합니다"), N),
        # 일반 의료 낱말·응급 어휘는 안전망이 아니다(검수 모델이 정한다).
        (_finding("HARD", "HOSPITAL_FACT", "응급 안내와 통증 효과 설명", quote=CHEST), N),
        (_finding("HARD", "STYLE", "보장성 보험 안내 문장", quote=OPENING), N),
        # 발행을 막지 않는 지적은 등급 대상이 아니다.
        (_finding("SOFT", "STYLE", "문장이 깁니다", critical=True), N),
        (_finding("HARD", "MEDICAL_SAFETY", "x", target="MUST_USE_MESSAGE", critical=True), N),
    ],
)
def test_finding_classification_by_reviewer_and_override(finding, expected) -> None:
    assert classify_finding(finding, CONTENT) is expected


@pytest.mark.parametrize("term", ["효과", "증상", "통증", "응급", "119", "용량", "시술", "약을 드세요"])
def test_generic_and_emergency_words_do_not_override_the_reviewer(term) -> None:
    """2026-10-14 대표 결정: 검수 모델의 판정을 덮는 어휘는 완치·보장·최저가·할인뿐이다(삭제 편집
    안전 검사 어휘 `MEDICAL_SAFETY_LEXICON`과 다르다)."""

    assert not mentions_critical_override(f"이 문장은 {term}에 관한 문장입니다")
    assert mentions_critical_override("완치를 보장하는 최저가 할인")


def test_override_words_are_checked_in_the_quote_even_without_the_body() -> None:
    finding = _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙", quote=PHILOSOPHY)

    assert classify_finding(finding) is N
    assert classify_finding({**finding, "quote": "완치를 보장합니다"}) is C
    # 본문을 몰라도 지적 문구는 보지 않는다.
    assert classify_finding({**finding, "message": "100% 완치로 읽힐 수 있습니다"}) is N


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
    critical = {
        "findings": [_finding("HARD", "MEDICAL_SAFETY", "위험한 오정보", quote=PHILOSOPHY, critical=True)]
    }
    fact = {"findings": [_finding("HARD", "HOSPITAL_FACT", "근거 없는 진료 원칙", quote=PHILOSOPHY)]}

    assert classify_block("CONTENT_AI_HARD_FINDING", review=critical, content=CONTENT) is C
    assert classify_block("CONTENT_AI_HARD_FINDING", review=fact, content=CONTENT) is N
    assert review_has_critical_finding(critical, CONTENT)
    assert not review_has_critical_finding(fact, CONTENT)
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
    assert flag_findings_are_critical(
        [_finding("HARD", "MEDICAL_SAFETY", quote=PHILOSOPHY, critical=True)], CONTENT
    )


def test_only_critical_hard_is_the_model_declared_terminal() -> None:
    """의료 내용 없는 병원 사실 HARD는 교정·주제 교체 계단의 종착이 아니다 — 표본처럼 다시 쓴다(W4)."""

    fact = {"findings": [_finding("HARD", "HOSPITAL_FACT", "수련기관이 다릅니다", quote=TRAINING)]}
    safety = {"findings": [_finding("HARD", "MEDICAL_SAFETY", quote=TRAINING, critical=True)]}

    assert not has_model_declared_hard_finding(fact, CONTENT)
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


@pytest.fixture(autouse=True)
def _tiers_and_alerts_on(monkeypatch):
    """이 파일은 기본 꺼짐인 W4 등급·D35 알림을 켠 동작을 고정한다(꺼진 동작은 main과 같다)."""

    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    monkeypatch.setattr(_settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", True)


# ── 2026-10-14 재설계: 등급은 검수 모델의 `critical`이 정하고 어휘는 안전망이다 ──────────────


@pytest.mark.parametrize(
    ("critical", "expected"),
    [(False, N), (True, C), (None, C), ("false", C), (0, C)],
)
def test_reviewer_critical_field_decides_and_missing_is_critical(critical, expected) -> None:
    finding = _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙", quote=PHILOSOPHY)
    if critical is None:
        finding.pop("critical")
    else:
        finding["critical"] = critical

    assert classify_finding(finding, CONTENT) is expected


def test_lexicon_overrides_a_reviewer_false() -> None:
    """검수 모델이 false라 해도 글의 문장에 안전망 어휘가 있으면 Critical이다(추가 안전망)."""

    finding = _finding("HARD", "HOSPITAL_FACT", "근거 없는 진료 원칙", quote=GUARANTEE)

    assert finding["critical"] is False
    assert classify_finding(finding, CONTENT) is C


def test_reviewer_false_decides_even_for_a_medical_safety_kind() -> None:
    """2026-10-14 대표 결정: 종류 라벨이 아니라 검수 모델의 `critical`이 정한다."""

    finding = _finding("HARD", "MEDICAL_SAFETY", "승인 자료에 없는 진료 원칙", quote=PHILOSOPHY)

    assert classify_finding(finding, CONTENT) is N
    assert classify_finding({**finding, "critical": True}, CONTENT) is C


def test_review_parser_reads_the_critical_field_strictly() -> None:
    from app.services import content_ai_review as car

    def parse(**extra):
        return car._parse_finding(
            {"severity": "HARD", "kind": "HOSPITAL_FACT", "quote": "q", "message": "m", **extra}
        )

    assert parse(critical=False).critical is False
    assert parse(critical=True).critical is True
    assert parse().critical is None
    assert parse(critical="false").critical is None
    assert parse(critical=False).payload()["critical"] is False
    # v3 출력은 판정이 없어도 키를 싣는다(null = Critical) — 키 없는 옛 저장 지적과 구분된다.
    assert "critical" in parse().payload() and parse().payload()["critical"] is None
    # 저장된 기록에서 되살릴 때도 같다 — 옛 v2 기록(필드 없음)은 None(Critical)이다.
    stored = car.review_from_payload(
        {"status": "REVISE", "findings": [parse(critical=False).payload(), parse().payload()]}
    )
    assert [finding.critical for finding in stored.findings] == [False, None]


def test_stored_flag_findings_without_the_key_use_only_the_override_words() -> None:
    """옛 저장 지적(키 없음)은 판정받은 적이 없다 — HARD이고 완치·보장·최저가·할인이 있을 때만 Critical."""

    def legacy(message="근거 없는 진료 원칙", quote=PHILOSOPHY, kind="HOSPITAL_FACT", severity="HARD"):
        finding = _finding(severity, kind, message, quote=quote)
        finding.pop("critical")
        return finding

    assert flag_findings_are_critical([legacy()], CONTENT) is False
    assert flag_findings_are_critical([legacy(kind="MEDICAL_SAFETY", message="응급 안내 누락")], CONTENT) is False
    assert flag_findings_are_critical([legacy(quote=GUARANTEE)], CONTENT) is True
    assert flag_findings_are_critical([legacy(quote="첫 진료는")], CONTENT) is True  # 인용 문장에 할인
    assert flag_findings_are_critical([legacy(message="완치를 보장한다는 표현")], CONTENT) is False
    assert flag_findings_are_critical([legacy(quote=GUARANTEE, severity="UNCERTAIN")], CONTENT) is False
    # v3 출력은 키가 있다 — null·true는 Critical, false는 안전망 어휘가 없으면 비-Critical.
    nulled = _finding("HARD", "HOSPITAL_FACT", "근거 없는 진료 원칙", quote=PHILOSOPHY, critical=None)
    assert flag_findings_are_critical([nulled], CONTENT) is True
    assert flag_findings_are_critical([legacy(), nulled], CONTENT) is True
    assert flag_findings_are_critical(
        [_finding("HARD", "HOSPITAL_FACT", "근거 없는 진료 원칙", quote=PHILOSOPHY)], CONTENT
    ) is False
    assert flag_findings_are_critical(None, CONTENT) is True


def test_review_schema_requires_the_critical_field_and_bumps_the_version() -> None:
    from app.services import content_ai_review as car

    item = car.REVIEW_TOOL["input_schema"]["properties"]["findings"]["items"]
    assert item["properties"]["critical"]["type"] == "boolean"
    assert "critical" in item["required"]
    assert car.REVIEW_SCHEMA_VERSION == "content-review-v4"
    prompt = car._SYSTEM_PROMPT
    # 2026-10-15 대표 확정 정의 — 두 가지뿐이고, 누락은 어떤 경우에도 Critical·차단이 아니다.
    assert "critical=true는 아래 두 가지뿐입니다" in prompt
    assert "환자유인·불법 의료광고" in prompt and "무책임한 허위정보" in prompt
    assert "흉통이 있어도 집에서 쉬면 됩니다" in prompt and "혈변은 걱정할 필요 없습니다" in prompt
    assert "누락(무엇이 빠졌다·덧붙이라는 지적)은 어떤 경우에도 critical이 아니고" in prompt
    assert "HARD도 UNCERTAIN도 아닙니다" in prompt
    # 옛 넓은 정의(응급 안내 누락 = Critical, 애매하면 true)는 남지 않는다.
    assert "확실하지 않으면 critical=true" not in prompt
    assert "안내의 누락이나 오안내" not in prompt


def test_inducement_marker_reads_only_the_posts_own_text() -> None:
    """옛 v3 재등급의 유인 표지도 검수자의 지적 문구가 아니라 글 자신의 문장만 본다(2026-10-15)."""

    def v3(message: str, quote: str) -> dict:
        return {"severity": "HARD", "kind": "HOSPITAL_FACT", "message": message, "quote": quote,
                "critical": True}

    content = {**CONTENT, "body": f"## 안내\n{PHILOSOPHY}\n첫 진료는 최저가로 모십니다.\n"}
    # 지적 문구에만 '최저가'가 있고 인용 문장은 깨끗하다 → 비-Critical.
    assert classify_finding(v3("최저가로 읽힐 수 있다", PHILOSOPHY), content) is N
    # 인용 문장 자체에 '최저가'가 있다 → Critical.
    assert classify_finding(v3("가격 표현", "첫 진료는 최저가로 모십니다."), content) is C
    # 글에 없는 인용은 보지 않는다.
    assert classify_finding(v3("가격 표현", "병원에 없는 최저가 문장"), content) is N

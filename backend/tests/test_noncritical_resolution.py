"""비-Critical 지적의 결정적 해소(W4) — 지적 문장 삭제, 교정 제안(LLM)·숫자 치환 없음.

감사 실례(2026-10-14): 진료 철학 문장(근거 없는 병원 주장)과 회복 기간 2주 vs 승인 자료 3주가 교정·
주제 교체 예산을 다 썼다. 의료 내용이 없는 병원 사실은 Critical이 아니다 — 지적 문장을 지우고 첫 판정
모델로 재검수한다. 숫자를 승인 값으로 바꿔 넣지 않는다(Codex P1-3: 5mg→20mg, 회복 8주←예약 8주,
개원 20년←경력 20년). 교정본은 여전히 그 hash에 묶인 PASS가 있어야 발행된다.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import content_minimal_correction as mc
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.content_publication import _blocking_ai_review_state

PHILOSOPHY_SENTENCE = "본원은 환자 한 분 한 분의 이야기를 끝까지 듣는 것을 진료 원칙으로 삼습니다."
OPENING_SENTENCE = "본원은 개원 15년째 같은 자리를 지키고 있습니다."
RECOVERY_SENTENCE = "수술 후 일상생활 복귀까지는 보통 2주가 걸립니다."
EMERGENCY_MESSAGE = "검은 변이 나오면 119·응급실 안내가 필요합니다"
FILLER = "허리 통증은 원인이 다양해 진찰과 영상 검사로 원인을 확인합니다. " * 40


def _content(*sentences: str) -> dict:
    body = "## 안내\n" + FILLER + " ".join(sentences) + "\n\n## 내원 전 확인\n통증이 오래가면 진료를 받으세요."
    return {
        "title": "허리 수술 뒤 회복, 얼마나 걸릴까요?",
        "body": body,
        "meta_description": "회복 기간을 안내합니다.",
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": [],
    }


def _hospital():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="마포탑정형외과의원",
        director_name="이현진",
        director_career="정형외과 전문의",
        treatments=["허리 수술"],
        specialties=["정형외과"],
        keywords=[],
        region=["서울 마포구"],
    )


def _philosophy(**extra):
    values = {
        "id": uuid.uuid4(),
        "positioning_statement": "수술 후 일상생활 복귀까지 보통 3주가 걸린다고 안내합니다.",
        "doctor_voice": "",
        "patient_promise": "",
        "content_principles": [],
        "treatment_narratives": [],
        "must_use_messages": [],
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _finding(severity, kind, message, quote="", critical=False):
    return {"severity": severity, "kind": kind, "message": message, "quote": quote, "critical": critical}


def _review(content, *findings, status="REVISE"):
    return {
        "status": status,
        "blocking": True,
        "findings": list(findings),
        "schema_version": "content-review-v2",
        "candidate_sha256": candidate_sha256(content),
        "coverage": candidate_review_coverage(content),
    }


def _result(content, status=ContentAiReviewStatus.PASS, findings=()) -> ContentAiReview:
    return ContentAiReview(
        status=status,
        confidence=0.9,
        findings=tuple(findings),
        summary="재검수",
        model="sonnet-test",
        candidate_sha256=candidate_sha256(content),
        coverage=candidate_review_coverage(content),
        provider_attempted=True,
    )


def _sources(philosophy=None):
    return mc.approved_fact_texts(_hospital(), philosophy or _philosophy())


def test_numeric_replacement_is_gone() -> None:
    """오케스트레이터 결정(2026-10-14): 결정적 숫자 치환은 없다."""

    assert not hasattr(mc, "numeric_replacement")


@pytest.mark.parametrize(
    ("sentence", "message", "approved"),
    [
        # 대기실 20석(본문) vs 승인 자료 12석 — 의료 내용 없는 수치 불일치.
        ("본원 대기실은 20석 규모로 꾸며져 있습니다.", "대기실 규모가 승인 자료와 다릅니다",
         "대기실은 12석입니다."),
        # 개원 15년(본문) vs 승인 자료의 경력 20년 — 다른 사실의 같은 단위.
        (OPENING_SENTENCE, "개원 연도가 승인 자료와 다릅니다", "원장은 경력 20년의 전문의입니다."),
    ],
)
def test_noncritical_number_mismatch_deletes_the_sentence(sentence, message, approved) -> None:
    content = _content(sentence)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", message, sentence))

    fix = mc.plan_noncritical_fixes(content, review)

    assert fix is not None
    [target] = fix.plan.targets
    assert fix.decisions[target.key].replacement is None
    corrected = mc.apply_corrections(content, fix.plan, fix.decisions)
    mc.verify_correction_scope(
        content, corrected, fix.plan, sources=_sources(_philosophy(doctor_voice=approved))
    )
    assert sentence not in corrected["body"]
    assert "12석" not in corrected["body"] and "20년" not in corrected["body"]


def test_recovery_period_mismatch_is_critical_when_the_reviewer_says_so() -> None:
    """회복 기간 8주(본문) vs 승인 자료의 다음 예약 8주 — 수술 회복은 의료 내용이다. 등급은 검수 모델이
    정하므로(일반 의료 낱말은 안전망이 아니다) 검수 모델이 Critical로 판정하면 이 계단이 맡지 않는다."""

    sentence = "수술 후 회복 기간은 보통 6주입니다."
    content = _content(sentence)
    review = _review(
        content, _finding("HARD", "HOSPITAL_FACT", "회복 기간이 다릅니다", sentence, critical=True)
    )

    assert mc.plan_noncritical_fixes(content, review) is None


def test_partial_quote_never_deletes_emergency_guidance_in_the_same_sentence() -> None:
    """Codex 2차 재현: '진료 원칙' 부분만 인용해도 같은 문장의 '흉통에는 즉시 119'를 지우지 않는다."""

    sentence = "본원은 환자의 이야기를 듣는 것을 원칙으로 하며, 흉통에는 즉시 119에 연락하셔야 합니다."
    content = _content(sentence)
    review = _review(
        content,
        _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙", "환자의 이야기를 듣는 것을 원칙으로"),
    )

    assert mc.plan_noncritical_fixes(content, review) is None


def test_emergency_guidance_must_survive_any_deletion() -> None:
    before = {"body": "응급 시에는 119에 연락하세요. 본원은 원칙을 지킵니다."}

    assert mc.emergency_guidance_lost(before, {"body": "본원은 원칙을 지킵니다."})
    assert not mc.emergency_guidance_lost(before, {"body": "응급 시에는 119에 연락하세요."})


def test_dosage_mismatch_is_critical_and_never_resolved_here() -> None:
    """투약량(5mg vs 승인 자료 20mL)은 종류가 병원 사실이어도 Critical이다 — 이 계단이 맡지 않는다."""

    sentence = "증상이 있으면 하루 5mg을 복용합니다."
    content = _content(sentence)
    review = _review(
        content, _finding("HARD", "HOSPITAL_FACT", "투약량이 승인 자료(20mL)와 다릅니다", sentence)
    )

    assert mc.plan_noncritical_fixes(content, review) is None


def test_plan_deletes_unsupported_philosophy_and_opening_sentences() -> None:
    content = _content(PHILOSOPHY_SENTENCE, OPENING_SENTENCE)
    review = _review(
        content,
        _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 철학", PHILOSOPHY_SENTENCE),
        _finding("HARD", "HOSPITAL_FACT", "개원 연도가 승인 자료와 다릅니다", OPENING_SENTENCE),
    )

    fix = mc.plan_noncritical_fixes(content, review)

    assert fix is not None
    assert {fix.decisions[t.key].replacement for t in fix.plan.targets} == {None}
    corrected = mc.apply_corrections(content, fix.plan, fix.decisions)
    mc.verify_correction_scope(content, corrected, fix.plan, sources=_sources())
    assert PHILOSOPHY_SENTENCE not in corrected["body"]
    assert OPENING_SENTENCE not in corrected["body"]


def test_uncertain_medical_sentence_is_critical_and_left_to_the_existing_ladder() -> None:
    """의료 안전 지적은 확신 부족이어도 Critical이다(fail closed) — 결정적 삭제 계단이 맡지 않는다."""

    sentence = "이 통증은 대부분 2주 안에 저절로 낫습니다."
    content = _content(sentence)
    review = _review(content, _finding("UNCERTAIN", "MEDICAL_SAFETY", "근거가 불분명합니다", sentence))

    assert mc.plan_noncritical_fixes(content, review) is None


@pytest.mark.parametrize(
    "findings",
    [
        # Critical(의료 안전 HARD)이 하나라도 있으면 이 계단이 맡지 않는다.
        [
            _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE),
            _finding("HARD", "MEDICAL_SAFETY", "위험한 오정보", RECOVERY_SENTENCE),
        ],
        # 응급 안내 누락은 Critical이다.
        [_finding("UNCERTAIN", "MEDICAL_SAFETY", EMERGENCY_MESSAGE)],
        # 인용 없는 지적은 문장으로 짚을 수 없다.
        [_finding("HARD", "HOSPITAL_FACT", "개원 연도를 확인할 수 없습니다")],
        # 일부만 짚히면 나머지가 재검수를 다시 막는다 — 맡지 않는다.
        [
            _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE),
            _finding("UNCERTAIN", "HOSPITAL_FACT", "장비 확인 불가"),
        ],
        # 내용을 모르는 병원 사실은 Critical이다(fail closed).
        [_finding("HARD", "HOSPITAL_FACT", "승인 자료에서 확인할 수 없습니다", RECOVERY_SENTENCE, None)],
    ],
)
def test_plan_refuses_anything_it_cannot_resolve_deterministically(findings) -> None:
    content = _content(PHILOSOPHY_SENTENCE, RECOVERY_SENTENCE)

    assert mc.plan_noncritical_fixes(content, _review(content, *findings)) is None


def test_must_use_sentence_is_never_deleted() -> None:
    must_use = PHILOSOPHY_SENTENCE
    content = _content(PHILOSOPHY_SENTENCE)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE))

    assert mc.plan_noncritical_fixes(content, review, must_use_messages=[must_use]) is None


async def test_run_deletes_rereviews_with_the_first_judgement_model_and_never_proposes() -> None:
    content = _content(PHILOSOPHY_SENTENCE)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE))
    calls: list[dict] = []

    async def fake_review(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["content"])

    outcome = await mc.run_noncritical_resolution(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        rereviews_used=0,
        review_fn=fake_review,
    )

    assert outcome.status == "PASS"
    assert outcome.rereviews == 1
    assert outcome.passes == 0  # Critical 교정 상한을 쓰지 않는다
    assert PHILOSOPHY_SENTENCE not in outcome.content["body"]
    [call] = calls
    assert call["model"] == settings.POST_PUBLISH_AI_REVIEW_MODEL
    assert call["correction_context"]["addressed"][0]["before"] == PHILOSOPHY_SENTENCE
    # 교정본에 묶인 PASS다 — 게이트가 교정본을 통과시킨다.
    assert outcome.review.candidate_sha256 == candidate_sha256(outcome.content)


async def test_run_stops_at_its_own_rereview_limit() -> None:
    content = _content(PHILOSOPHY_SENTENCE, OPENING_SENTENCE, "본원 대기실은 넓습니다.")
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE))

    async def flags_the_next_sentence(**kwargs):
        corrected = kwargs["content"]
        quote = OPENING_SENTENCE if OPENING_SENTENCE in corrected["body"] else "본원 대기실은 넓습니다."
        return _result(
            corrected,
            status=ContentAiReviewStatus.REVISE,
            findings=(
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.HOSPITAL_FACT,
                    "승인 자료에 없는 진료 원칙 문장",
                    quote=quote,
                    critical=False,
                ),
            ),
        )

    outcome = await mc.run_noncritical_resolution(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        rereviews_used=0,
        review_fn=flags_the_next_sentence,
    )

    assert outcome.status == "BLOCKED"
    assert outcome.rereviews == mc.NONCRITICAL_REREVIEW_LIMIT

    exhausted = await mc.run_noncritical_resolution(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        rereviews_used=mc.NONCRITICAL_REREVIEW_LIMIT,
        review_fn=flags_the_next_sentence,
    )
    assert exhausted.status == "EXHAUSTED"
    assert exhausted.content is None


async def test_run_hands_a_critical_rereview_back_to_the_existing_ladder() -> None:
    content = _content(PHILOSOPHY_SENTENCE, RECOVERY_SENTENCE)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE))

    async def critical(**kwargs):
        return _result(
            kwargs["content"],
            status=ContentAiReviewStatus.REVISE,
            findings=(
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.MEDICAL_SAFETY,
                    "위험한 오정보",
                    quote=RECOVERY_SENTENCE,
                ),
            ),
        )

    outcome = await mc.run_noncritical_resolution(
        hospital=_hospital(),
        philosophy=_philosophy(),
        content=content,
        review=review,
        content_brief=None,
        must_use_messages=[],
        rereviews_used=0,
        review_fn=critical,
    )

    assert outcome.status == "BLOCKED"
    assert outcome.rereviews == 1


def test_gate_still_requires_a_pass_bound_to_the_corrected_body() -> None:
    """교정 기록이 있으면 교정본 hash에 묶인 PASS 없이는 통과하지 않는다(계약 그대로)."""

    content = _content(PHILOSOPHY_SENTENCE)
    item = SimpleNamespace(
        **content,
        content_type="COLUMN",
        status="DRAFT",
        first_published_at=None,
        content_brief={},
        essence_check_summary={
            "ai_review": _review(content, _finding("HARD", "HOSPITAL_FACT", "진료 철학", PHILOSOPHY_SENTENCE)),
            "auto_correction": {"corrected_sha256": candidate_sha256(content)},
        },
    )
    item.essence_check_summary["ai_review"]["candidate_sha256"] = "older-body"

    assert _blocking_ai_review_state(item)[0] == "STALE"


@pytest.fixture(autouse=True)
def _tiers_and_alerts_on(monkeypatch):
    """이 파일은 기본 꺼짐인 W4 등급·D35 알림을 켠 동작을 고정한다(꺼진 동작은 main과 같다)."""

    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    monkeypatch.setattr(_settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", True)

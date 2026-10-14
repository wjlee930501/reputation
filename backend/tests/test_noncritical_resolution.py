"""비-Critical 지적의 결정적 해소(W4) — 지적 문장 삭제·승인 수치 치환, 교정 제안(LLM) 없음.

감사 실례(2026-10-14): 진료 철학 문장(근거 없는 병원 주장)과 회복 기간 2주 vs 승인 자료 3주가 교정·
주제 교체 예산을 다 썼다. 이 둘은 Critical이 아니다 — 지우거나 승인 값으로 바꾸고 첫 판정 모델로
재검수한다. 교정본은 여전히 그 hash에 묶인 PASS가 있어야 발행된다.
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

PHILOSOPHY_SENTENCE = "본원은 진단부터 재활까지 의료진이 정보를 공유하는 것을 진료 원칙으로 합니다."
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


def _finding(severity, kind, message, quote=""):
    return {"severity": severity, "kind": kind, "message": message, "quote": quote}


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


def test_numeric_mismatch_takes_the_single_approved_value() -> None:
    assert (
        mc.numeric_replacement(RECOVERY_SENTENCE, _sources())
        == "수술 후 일상생활 복귀까지는 보통 3주가 걸립니다."
    )


@pytest.mark.parametrize(
    ("sentence", "philosophy"),
    [
        # 같은 단위의 승인 수치가 둘이면 애매하다.
        (RECOVERY_SENTENCE, _philosophy(doctor_voice="재활은 6주, 복귀는 3주를 안내합니다.")),
        # 단위 없는 숫자는 바꾸지 않는다.
        ("수술 후 2번 내원합니다.", _philosophy()),
        # 승인 자료에 없는 수치가 둘이면 어느 것인지 모른다.
        ("수술 후 2주에서 5주가 걸립니다.", _philosophy()),
    ],
)
def test_ambiguous_numbers_are_not_replaced(sentence, philosophy) -> None:
    assert mc.numeric_replacement(sentence, _sources(philosophy)) is None


def test_plan_deletes_unsupported_philosophy_and_replaces_the_number() -> None:
    content = _content(PHILOSOPHY_SENTENCE, RECOVERY_SENTENCE)
    review = _review(
        content,
        _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 철학", PHILOSOPHY_SENTENCE),
        _finding("HARD", "HOSPITAL_FACT", "회복 기간이 승인 자료와 다릅니다", RECOVERY_SENTENCE),
    )

    fix = mc.plan_noncritical_fixes(content, review, sources=_sources())

    assert fix is not None
    decisions = {target.sentence: fix.decisions[target.key] for target in fix.plan.targets}
    assert decisions[PHILOSOPHY_SENTENCE].replacement is None
    assert decisions[RECOVERY_SENTENCE].replacement == "수술 후 일상생활 복귀까지는 보통 3주가 걸립니다."
    corrected = mc.apply_corrections(content, fix.plan, fix.decisions)
    mc.verify_correction_scope(content, corrected, fix.plan, sources=_sources())
    assert PHILOSOPHY_SENTENCE not in corrected["body"]
    assert "보통 3주가 걸립니다" in corrected["body"]


def test_uncertain_medical_sentence_is_deleted_not_rewritten() -> None:
    sentence = "이 통증은 대부분 2주 안에 저절로 낫습니다."
    content = _content(sentence)
    review = _review(content, _finding("UNCERTAIN", "MEDICAL_SAFETY", "근거가 불분명합니다", sentence))

    fix = mc.plan_noncritical_fixes(content, review, sources=_sources())

    assert fix is not None
    [target] = fix.plan.targets
    # 의료 안전 지적은 수치를 바꾸지 않고 지운다(승인 자료는 병원 사실이다).
    assert fix.decisions[target.key].replacement is None


@pytest.mark.parametrize(
    "findings",
    [
        # Critical(의료 안전 HARD)이 하나라도 있으면 이 계단이 맡지 않는다.
        [
            _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE),
            _finding("HARD", "MEDICAL_SAFETY", "위험한 오정보", RECOVERY_SENTENCE),
        ],
        # 응급 안내 누락은 Critical이다.
        [_finding("UNCERTAIN", "MEDICAL_SAFETY", EMERGENCY_MESSAGE)],
        # 인용 없는 지적은 문장으로 짚을 수 없다.
        [_finding("HARD", "HOSPITAL_FACT", "심장 초음파를 확인할 수 없습니다")],
        # 일부만 짚히면 나머지가 재검수를 다시 막는다 — 맡지 않는다.
        [
            _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE),
            _finding("UNCERTAIN", "HOSPITAL_FACT", "장비 확인 불가"),
        ],
    ],
)
def test_plan_refuses_anything_it_cannot_resolve_deterministically(findings) -> None:
    content = _content(PHILOSOPHY_SENTENCE, RECOVERY_SENTENCE)

    assert mc.plan_noncritical_fixes(content, _review(content, *findings), sources=_sources()) is None


def test_must_use_sentence_is_never_deleted() -> None:
    must_use = "본원은 진단부터 재활까지 의료진이 정보를 공유하는 것을 진료 원칙으로 합니다."
    content = _content(PHILOSOPHY_SENTENCE)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE))

    assert (
        mc.plan_noncritical_fixes(content, review, must_use_messages=[must_use], sources=_sources())
        is None
    )


async def test_run_deletes_rereviews_with_the_first_judgement_model_and_never_proposes() -> None:
    content = _content(PHILOSOPHY_SENTENCE)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE))
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
    content = _content(PHILOSOPHY_SENTENCE, RECOVERY_SENTENCE)
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE))

    async def flags_the_next_sentence(**kwargs):
        corrected = kwargs["content"]
        quote = RECOVERY_SENTENCE if RECOVERY_SENTENCE in corrected["body"] else "통증이 오래가면 진료를 받으세요."
        return _result(
            corrected,
            status=ContentAiReviewStatus.REVISE,
            findings=(
                ContentAiFinding(
                    ContentAiFindingSeverity.HARD,
                    ContentAiFindingKind.HOSPITAL_FACT,
                    "다음 문장",
                    quote=quote,
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
    review = _review(content, _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE))

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
            "ai_review": _review(content, _finding("HARD", "HOSPITAL_FACT", "철학", PHILOSOPHY_SENTENCE)),
            "auto_correction": {"corrected_sha256": candidate_sha256(content)},
        },
    )
    item.essence_check_summary["ai_review"]["candidate_sha256"] = "older-body"

    assert _blocking_ai_review_state(item)[0] == "STALE"

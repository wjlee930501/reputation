import json
import uuid
from types import SimpleNamespace

import pytest

from app.services import content_ai_review
from app.services import content_public_review_backfill as backfill
from app.services.cost_guard import CostGuardDecision


def _expectation(**overrides):
    values = {
        "content_id": uuid.uuid4(),
        "hospital_id": uuid.uuid4(),
        "content_revision": 4,
        "candidate_hash": "a" * 64,
        "candidate": {"title": "제목", "body": "본문"},
        "brief": {"target_query": "질문"},
        "philosophy_id": uuid.uuid4(),
        "source_snapshot_hash": "c" * 64,
        "review_input_hash": "d" * 64,
    }
    values.update(overrides)
    return backfill._ReviewExpectation(**values)


def test_operation_identity_ignores_unrelated_revision_bumps() -> None:
    original = _expectation(content_revision=7)
    metadata_only_edit = _expectation(
        content_id=original.content_id,
        hospital_id=original.hospital_id,
        content_revision=8,
        candidate_hash=original.candidate_hash,
        candidate=original.candidate,
        brief=original.brief,
        philosophy_id=original.philosophy_id,
        source_snapshot_hash=original.source_snapshot_hash,
        review_input_hash=original.review_input_hash,
    )
    changed_candidate = _expectation(
        content_id=original.content_id,
        hospital_id=original.hospital_id,
        content_revision=8,
        candidate_hash="e" * 64,
        philosophy_id=original.philosophy_id,
        source_snapshot_hash=original.source_snapshot_hash,
        review_input_hash=original.review_input_hash,
    )

    assert metadata_only_edit.operation_key == original.operation_key
    assert changed_candidate.operation_key != original.operation_key


def test_result_counts_are_mutually_exclusive_and_unresolved_is_explicit() -> None:
    result = backfill.PublicReviewBackfillResult(allowlisted=4)
    for outcome in ("current", "cleared", "blocked", "costblocked"):
        result = result.with_outcome(outcome)

    assert sum(getattr(result, outcome) for outcome in backfill._OUTCOMES) == 4
    assert result.unresolved == 2
    assert result.to_dict()["unresolved"] == 2


@pytest.mark.parametrize(
    "findings",
    [
        {"severity": "HARD", "message": "객체지만 배열 아님"},
        "배열 아님",
        None,
        [{}],
        [{"severity": "HARD", "kind": "HOSPITAL_FACT", "message": ""}],
        [None],
    ],
)
def test_malformed_findings_can_never_become_a_high_confidence_pass(findings) -> None:
    with pytest.raises(ValueError):
        content_ai_review._parse_response(
            json.dumps(
                {
                    "decision": "PASS",
                    "confidence": 0.99,
                    "findings": findings,
                    "summary": "형식 오류",
                }
            ),
            reviewed_content={"title": "안내", "body": "본문"},
        )


@pytest.mark.parametrize(
    "confidence",
    ["NaN", "Infinity", "-Infinity", -0.01, 1.01, True, None, "not-a-number"],
)
def test_invalid_confidence_can_never_become_pass(confidence) -> None:
    with pytest.raises(ValueError):
        content_ai_review._parse_response(
            json.dumps(
                {
                    "decision": "PASS",
                    "confidence": confidence,
                    "findings": [],
                    "summary": "형식 오류",
                }
            ),
            reviewed_content={"title": "안내", "body": "본문"},
        )


@pytest.mark.parametrize("kind", ["HOSPITAL_FACT", "MEDICAL_SAFETY"])
def test_soft_label_cannot_downgrade_fact_or_medical_safety(kind) -> None:
    review = content_ai_review._parse_response(
        json.dumps(
            {
                "decision": "REVISE",
                "confidence": 0.99,
                "findings": [
                    {
                        "severity": "SOFT",
                        "kind": kind,
                        "message": "근거가 확인되지 않은 안전 관련 주장",
                    }
                ],
                "summary": "상충된 분류",
            }
        ),
        reviewed_content={"title": "안내", "body": "본문"},
    )

    assert review.blocking_findings
    assert review.blocking_findings[0].severity.value == "UNCERTAIN"


def test_candidate_and_brief_use_only_current_stored_public_fields() -> None:
    references = [{"title": "공식 자료", "url": "https://example.test"}]
    item = SimpleNamespace(
        title="제목",
        body="전체 본문",
        meta_description="설명",
        faq_question="질문?",
        faq_answer_summary="답변",
        references_list=references,
        content_brief={"target_query": "환자 질문", "schema_version": "content-brief-v2"},
    )

    candidate = backfill._candidate(item)

    assert candidate == {
        "title": "제목",
        "body": "전체 본문",
        "meta_description": "설명",
        "faq_question": "질문?",
        "faq_answer_summary": "답변",
        "references": references,
    }
    assert backfill._brief(item) == item.content_brief
    item.content_brief = {"planning_reason": "아직 승인 브리프 아님"}
    assert backfill._brief(item) is None


def test_review_input_hash_uses_exact_provider_visible_projection() -> None:
    hospital = SimpleNamespace(
        name="테스트병원",
        director_name="김원장",
        director_career="전문의",
        address="서울",
        phone="02-0000-0000",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://example.test",
        region=["서울"],
        specialties=["피부과"],
        treatments=[f"진료 {i}" for i in range(30)],
    )
    philosophy = SimpleNamespace(
        positioning_statement="근거 중심",
        must_use_messages=[],
        avoid_messages=[],
        medical_ad_risk_rules=[],
    )
    candidate = {"title": "제목", "body": "본문"}
    brief = {
        "target_query": "환자 질문",
        "operator_notes": ["공급자에 전달되지 않는 필드"],
    }
    before = backfill._review_input_hash(hospital, philosophy, candidate, brief)
    # Omitted brief fields and values beyond the review projection's list cap do
    # not grant a fresh retry budget for identical provider input.
    brief["operator_notes"] = ["변경"]
    hospital.treatments = [*hospital.treatments, "31번째 미사용 진료"]
    unchanged = backfill._review_input_hash(hospital, philosophy, candidate, brief)
    hospital.director_career = "변경된 경력"

    assert unchanged == before
    assert backfill._review_input_hash(hospital, philosophy, candidate, brief) != before


@pytest.mark.asyncio
async def test_supplied_cost_block_has_typed_diagnostic_and_never_reserves_or_calls(
    monkeypatch,
) -> None:
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("supplied cost decision must bypass reservation and provider")

    monkeypatch.setattr(content_ai_review.cost_guard, "reserve", unexpected)
    monkeypatch.setattr(content_ai_review.cost_guard, "record_provider_call", unexpected)

    review = await content_ai_review.review_generated_content(
        hospital=SimpleNamespace(),
        philosophy=SimpleNamespace(),
        content={"title": "안내", "body": "본문"},
        content_brief=None,
        cost_decision=CostGuardDecision(False, "일일 상한"),
        logical_call_id="logical-1",
        attempt_id="attempt-1",
    )

    assert review.provider_attempted is False
    assert (
        review.unavailable_reason
        == content_ai_review.ContentAiReviewUnavailableReason.COST_BLOCKED
    )


@pytest.mark.asyncio
async def test_supplied_allowed_decision_refunds_when_provider_is_unconfigured(
    monkeypatch,
) -> None:
    receipt = SimpleNamespace(id="receipt")
    settled = []

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("supplied cost decision must not reserve twice")

    async def settle(value, *, consumed_units):
        settled.append((value, consumed_units))

    monkeypatch.setattr(content_ai_review.cost_guard, "reserve", unexpected)
    monkeypatch.setattr(content_ai_review.cost_guard, "settle_reservation", settle)
    monkeypatch.setattr(content_ai_review.settings, "ANTHROPIC_API_KEY", "")

    review = await content_ai_review.review_generated_content(
        hospital=SimpleNamespace(),
        philosophy=SimpleNamespace(),
        content={"title": "안내", "body": "본문"},
        content_brief=None,
        cost_decision=CostGuardDecision(True, receipt=receipt),
    )

    assert review.provider_attempted is False
    assert (
        review.unavailable_reason
        == content_ai_review.ContentAiReviewUnavailableReason.PROVIDER_UNCONFIGURED
    )
    assert settled == [(receipt, 0)]

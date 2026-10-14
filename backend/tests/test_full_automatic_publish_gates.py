"""D36(참고자료 없이 생성·발행)·D37(검수 장애면 먼저 발행) 발행 게이트·공개 표면 계약(2026-10-14).

- D36: 참고자료 필수 유형의 글도 검증된 참고자료가 없으면 막지 않는다. 참고자료가 **있으면** 종전대로
  인용 가능·실제 문서 확인이 현재여야 공개된다. 생성은 거절하지 않고 기록을 남긴다.
- D37: 독립 검수가 장애로 판정을 못 내면(UNAVAILABLE·공급자 오류) 발행한다. 비용 보류·설정 오류,
  자동 교정본, 다른 본문에 묶인 장애 기록은 종전대로 막는다. 결정적 검사(금지 표현 등)와 검수가
  **판정한** HARD는 그대로 막는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.content import ContentStatus, ContentType
from app.services import content_publication
from app.services.content_ai_review import (
    candidate_review_coverage,
    candidate_sha256,
    restamp_review_after_system_reference_change,
)
from app.services.content_engine import _validate_geo
from app.services.content_publication import (
    AI_REVIEW_DEFERRED_KEY,
    ai_review_deferred,
    apply_publication_assessment,
    assess_content_publication,
    has_required_references,
    public_surface_has_required_references,
)
from app.services.reference_requirement import (
    REFERENCES_OMITTED_KEY,
    REFERENCES_OMITTED_NO_AUTHORITATIVE_SOURCE,
    REFERENCES_OMITTED_NO_VERIFIED,
    reference_requirement_marker,
    references_block_when_empty,
    references_block_when_empty_for,
)


def _item(**overrides):
    image_hash = "a" * 64
    base = {
        "content_type": ContentType.DISEASE,
        "status": ContentStatus.DRAFT,
        "first_published_at": None,
        "title": "치질 진료 전 확인할 점",
        "body": "증상과 생활 불편을 확인한 뒤 진료 방향을 설명합니다.",
        "image_url": f"gs://reputation-images/content/{image_hash}-content.png",
        "image_content_hash": image_hash,
        "image_policy_verified_at": datetime.now(timezone.utc),
        "image_policy_version": content_publication.IMAGE_POLICY_VERSION,
        "meta_description": "진료 전 확인할 내용을 정리합니다.",
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": [],
        "content_brief": {},
        "content_philosophy_id": None,
        "essence_status": None,
        "essence_check_summary": None,
    }
    base.update(overrides)
    base.setdefault(
        "image_subject_hash",
        content_publication.image_subject_hash(base["content_type"], base["title"]),
    )
    return SimpleNamespace(**base)


@pytest.fixture
def aligned(monkeypatch):
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_args: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )


def _unavailable(item, *, reason="PROVIDER_ERROR", sha=None) -> dict:
    return {
        "status": "UNAVAILABLE",
        "findings": [],
        "blocking": False,
        "schema_version": "content-review-v2",
        "candidate_sha256": candidate_sha256(item) if sha is None else sha,
        "coverage": candidate_review_coverage(item),
        "unavailable_reason": reason,
    }


def _with_review(item, review: dict, **summary):
    item.essence_check_summary = {"ai_review": review, **summary}
    return item


# ── D36 ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("content_type", list(ContentType))
def test_no_content_type_blocks_on_empty_references(content_type) -> None:
    assert references_block_when_empty_for(content_type) is False
    item = _item(content_type=content_type, title="위염 진료 흐름")
    assert not references_block_when_empty(item)
    assert has_required_references(item)
    assert reference_requirement_marker(item)["block_when_empty"] is False


def test_medical_post_without_references_is_publishable(aligned) -> None:
    assessment = assess_content_publication(_item(), SimpleNamespace(id=uuid.uuid4()))

    assert assessment.publishable, assessment.code


def test_public_surface_shows_empty_references_but_hides_unverifiable_ones() -> None:
    assert public_surface_has_required_references(_item(references_list=[]))
    assert public_surface_has_required_references(_item(references_list=None))
    # 참고자료가 있으면 종전대로 인용 가능한 주소여야 한다(지어낸·화이트리스트 밖 주소는 숨긴다).
    assert not public_surface_has_required_references(
        _item(references_list=[{"title": "블로그", "url": "https://example.com/post"}])
    )


def test_generation_records_omitted_references_instead_of_rejecting() -> None:
    hospital = SimpleNamespace(name="강남병원", director_name="", region=[])
    result = {"title": "위염 진료 흐름", "body": "강남병원 위염 안내 3주", "references": []}

    _validate_geo(
        result,
        hospital,
        ContentType.DISEASE,
        reference_drop_notes=["kdca.go.kr(본문 불일치)"],
        references_required=True,
    )

    record = result[REFERENCES_OMITTED_KEY]
    assert record["reason"] == REFERENCES_OMITTED_NO_VERIFIED
    assert record["drop_notes"] == ["kdca.go.kr(본문 불일치)"]


def test_cost_post_records_that_no_authoritative_source_exists() -> None:
    hospital = SimpleNamespace(name="강남병원", director_name="", region=[])
    result = {"title": "강남 도수치료 비용 안내", "body": "강남병원 1회", "references": []}

    _validate_geo(result, hospital, ContentType.COLUMN, references_required=True)

    assert result[REFERENCES_OMITTED_KEY]["reason"] == REFERENCES_OMITTED_NO_AUTHORITATIVE_SOURCE


def test_generation_with_references_clears_a_stale_omission_record() -> None:
    hospital = SimpleNamespace(name="강남병원", director_name="", region=[])
    result = {
        "title": "위염",
        "body": "강남병원 1회",
        "references": [{"title": "위염", "url": "https://health.kdca.go.kr/x"}],
        REFERENCES_OMITTED_KEY: {"reason": "old"},
    }

    _validate_geo(result, hospital, ContentType.DISEASE, references_required=True)

    assert REFERENCES_OMITTED_KEY not in result


def test_gate_record_keeps_the_omission_and_deferral_records(aligned) -> None:
    item = _item()
    item = _with_review(item, _unavailable(item), **{REFERENCES_OMITTED_KEY: {"reason": "x"}})

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))
    apply_publication_assessment(item, assessment)

    assert item.essence_check_summary[REFERENCES_OMITTED_KEY] == {"reason": "x"}
    assert AI_REVIEW_DEFERRED_KEY in item.essence_check_summary


# ── D37 ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["PROVIDER_ERROR", "INVALID_RESPONSE", None])
def test_review_outage_publishes_first_with_a_hash_bound_marker(aligned, reason) -> None:
    item = _item()
    _with_review(item, _unavailable(item, reason=reason))

    assert ai_review_deferred(item)
    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))

    assert assessment.publishable, assessment.code
    marker = assessment.essence_summary[AI_REVIEW_DEFERRED_KEY]
    assert marker["candidate_sha256"] == candidate_sha256(item)
    assert marker["unavailable_reason"] == reason


@pytest.mark.parametrize(
    ("reason", "code"),
    [("COST_BLOCKED", "COST_BLOCKED"), ("PROVIDER_UNCONFIGURED", "CONTENT_AI_REVIEW_CONFIG_ERROR")],
)
def test_budget_hold_and_config_error_still_block(aligned, reason, code) -> None:
    item = _item()
    _with_review(item, _unavailable(item, reason=reason))

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))

    assert not ai_review_deferred(item)
    assert assessment.code == code


def test_outage_recorded_for_another_body_does_not_publish_this_one(aligned) -> None:
    item = _item()
    _with_review(item, _unavailable(item, sha="another-body"))

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))

    assert not ai_review_deferred(item)
    assert assessment.code == "CONTENT_AI_REVIEW_UNAVAILABLE"


def test_auto_corrected_body_still_needs_a_bound_pass(aligned) -> None:
    item = _item()
    _with_review(
        item,
        _unavailable(item),
        auto_correction={"corrected_sha256": candidate_sha256(item), "passes": 1},
    )

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))

    assert not ai_review_deferred(item)
    assert not assessment.publishable


def test_deterministic_checks_still_block_a_deferred_review(aligned) -> None:
    item = _item(body="부작용 없는 완벽한 치료를 보장합니다.")
    _with_review(item, _unavailable(item))

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))

    assert assessment.code == "FORBIDDEN_EXPRESSION"


def test_a_verdict_hard_finding_still_blocks(aligned) -> None:
    item = _item()
    _with_review(
        item,
        {
            **_unavailable(item),
            "status": "REVISE",
            "blocking": True,
            "findings": [
                {"severity": "HARD", "kind": "MEDICAL_SAFETY", "message": "위험한 오정보"}
            ],
        },
    )

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))

    assert assessment.code == "CONTENT_AI_HARD_FINDING"


def test_published_post_is_never_hidden_by_a_review_outage() -> None:
    item = _item(status=ContentStatus.PUBLISHED)
    _with_review(item, _unavailable(item, reason="COST_BLOCKED", sha="older"))

    assert ai_review_deferred(item)
    assert content_publication.public_candidate_review_safe(item)


def test_system_reference_change_keeps_the_outage_deferral() -> None:
    item = _item(references_list=[{"title": "위염", "url": "https://health.kdca.go.kr/x"}])
    _with_review(item, _unavailable(item))
    before = candidate_sha256(item)

    item.references_list = []
    restamp_review_after_system_reference_change(item, before)

    assert ai_review_deferred(item)


def test_correction_then_outage_then_reference_removal_never_publishes_first(aligned) -> None:
    """Codex P1-1 조합: 자동 교정 → 재검수 장애 → 시스템 참고자료 제거. 교정본은 지금 본문에 묶인
    PASS가 생길 때까지 선발행하지 않는다 — 참고자료 변경이 hash를 옮겨도 같다."""

    item = _item(references_list=[{"title": "위염", "url": "https://health.kdca.go.kr/x"}])
    _with_review(
        item,
        _unavailable(item),
        auto_correction={"corrected_sha256": candidate_sha256(item), "passes": 1},
    )
    assert not ai_review_deferred(item)
    before = candidate_sha256(item)

    item.references_list = []
    restamp_review_after_system_reference_change(item, before)

    summary = item.essence_check_summary
    assert summary["ai_review"]["candidate_sha256"] == candidate_sha256(item)
    # 교정본 표지도 새 본문을 따라간다 — "교정본은 반드시 재검수"가 그대로다.
    assert summary["auto_correction"]["corrected_sha256"] == candidate_sha256(item)
    assert not ai_review_deferred(item)
    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))
    assert assessment.code == "CONTENT_AI_REVIEW_UNAVAILABLE"
    assert AI_REVIEW_DEFERRED_KEY not in assessment.essence_summary


def test_any_correction_record_blocks_the_deferral_even_for_an_older_body(aligned) -> None:
    item = _item()
    _with_review(item, _unavailable(item), auto_correction={"corrected_sha256": "older-body"})

    assert not ai_review_deferred(item)


def test_deferral_marker_binds_the_current_public_text() -> None:
    from app.services.content_publication import (
        ai_review_deferral_marker,
        publication_text_sha256,
    )

    item = _item()
    _with_review(item, _unavailable(item))
    marker = ai_review_deferral_marker(item)

    assert marker["text_sha256"] == publication_text_sha256(item)
    item.body = item.body + " 수정"
    assert publication_text_sha256(item) != marker["text_sha256"]


def test_the_new_deferral_marker_wins_over_an_older_one(aligned) -> None:
    """Codex 2차 비차단: 참고자료 재표시 뒤 새 판정의 선발행 표지가 옛 표지에 덮이지 않는다."""

    item = _item()
    _with_review(item, _unavailable(item), **{AI_REVIEW_DEFERRED_KEY: {"candidate_sha256": "older"}})

    assessment = assess_content_publication(item, SimpleNamespace(id=uuid.uuid4()))
    apply_publication_assessment(item, assessment)

    assert item.essence_check_summary[AI_REVIEW_DEFERRED_KEY]["candidate_sha256"] == (
        candidate_sha256(item)
    )

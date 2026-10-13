"""범용 서술 각도(`fallback`) 글은 현재 본문 hash에 묶인 독립 검수가 있어야 발행된다."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.content import ContentStatus
from app.schemas.content import ContentRevisionSourceSnapshot
from app.services.content_ai_review import candidate_sha256
from app.services.content_publication import (
    _blocking_ai_review_state,
    fallback_narrative_requires_bound_review,
)
from app.workers.nightly_generation_batch import _stored_ai_review_unresolved


def _item(source: str = "fallback", review: dict | None = None, **overrides):
    values = {
        "title": "도수치료 후 생활 관리",
        "body": "본문",
        "meta_description": "설명",
        "faq_question": None,
        "faq_answer_summary": None,
        "references_list": [],
        "status": ContentStatus.DRAFT,
        "first_published_at": None,
        "content_brief": {"treatment_narrative": {"source": source, "angle": "각도"}},
        "essence_check_summary": {},
    }
    values.update(overrides)
    item = SimpleNamespace(**values)
    if review is not None:
        item.essence_check_summary = {"ai_review": review}
    return item


def _bound(item, status: str = "PASS", **extra) -> dict:
    return {"status": status, "schema_version": "3", "candidate_sha256": candidate_sha256(item), **extra}


def test_bound_pass_is_accepted():
    item = _item()
    item.essence_check_summary = {"ai_review": _bound(item)}

    assert _blocking_ai_review_state(item) is None
    assert _stored_ai_review_unresolved(item) is False


def test_bound_non_blocking_revise_is_accepted():
    item = _item()
    item.essence_check_summary = {"ai_review": _bound(item, "REVISE", blocking=False)}

    assert _blocking_ai_review_state(item) is None


@pytest.mark.parametrize(
    "review",
    [
        None,
        {"status": "PASS", "schema_version": "3"},
        {"status": "REVISE", "schema_version": "3", "blocking": False},
        {"status": "SKIPPED"},
    ],
)
def test_missing_or_unbound_review_is_stale_and_swept(review):
    item = _item(review=review)

    state = _blocking_ai_review_state(item)
    assert state is not None and state[0] == "STALE"
    assert _stored_ai_review_unresolved(item) is True


def test_review_bound_to_another_body_is_stale():
    item = _item(review={"status": "PASS", "schema_version": "3", "candidate_sha256": "0" * 64})

    assert _blocking_ai_review_state(item)[0] == "STALE"


@pytest.mark.parametrize("source", ["approved_philosophy", "hospital_profile"])
def test_approved_narrative_sources_keep_the_existing_rule(source):
    # 기존 규칙 그대로 — 검수 기록이 없거나 hash 없는 옛 PASS는 막지 않는다(별도 후속 과제).
    assert _blocking_ai_review_state(_item(source)) is None
    assert _blocking_ai_review_state(_item(source, {"status": "PASS"})) is None
    assert fallback_narrative_requires_bound_review(_item(source)) is False


def test_published_or_once_published_rows_are_not_hidden():
    published = _item(status=ContentStatus.PUBLISHED)
    withheld = _item(status=ContentStatus.WITHHELD, first_published_at=datetime(2026, 10, 1, tzinfo=UTC))

    assert _blocking_ai_review_state(published) is None
    assert _blocking_ai_review_state(withheld) is None


def test_unavailable_review_keeps_its_own_code():
    item = _item(review={"status": "UNAVAILABLE"})

    assert _blocking_ai_review_state(item)[0] == "UNAVAILABLE"


def test_revision_schema_accepts_fallback_narrative():
    snapshot = ContentRevisionSourceSnapshot.model_validate(
        {
            "schema_version": "content-brief-v2",
            "target_query": "도수치료 후 관리",
            "treatment_narrative": {"source": "fallback", "angle": "환자 언어로 설명"},
            "source_snapshot": {"hash": "a" * 64, "source_asset_ids": ["asset-1"]},
        }
    )

    assert snapshot.treatment_narrative.source == "fallback"

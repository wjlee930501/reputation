from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.content import (
    ContentRevisionGenerationProvenance,
    ContentRevisionResponse,
    ContentRevisionSourceSnapshot,
)


def test_revision_source_snapshot_parses_real_writer_input() -> None:
    snapshot = ContentRevisionSourceSnapshot.model_validate(
        {
            "schema_version": "content-brief-v2",
            "target_query": "진료 질문",
            "treatment_narrative": {
                "source": "approved_philosophy",
                "angle": "승인 근거를 환자 언어로 설명",
            },
            "source_snapshot": {"hash": "a" * 64, "source_asset_ids": ["asset-1"]},
            "must_use_messages": ["실제 작성 입력"],
        }
    )

    assert snapshot.treatment_narrative.angle == "승인 근거를 환자 언어로 설명"
    assert snapshot.model_extra == {"must_use_messages": ["실제 작성 입력"]}


def test_revision_source_snapshot_rejects_hash_only_shape() -> None:
    with pytest.raises(ValidationError):
        ContentRevisionSourceSnapshot.model_validate(
            {"source_snapshot": {"hash": "hash-only", "source_asset_ids": []}}
        )


def test_revision_generation_provenance_rejects_empty_object() -> None:
    with pytest.raises(ValidationError, match="grounded identifier"):
        ContentRevisionGenerationProvenance.model_validate({})


def test_historical_publication_response_keeps_unknown_evidence_null() -> None:
    revision_id = uuid.uuid4()
    content_id = uuid.uuid4()
    published_at = datetime(2026, 10, 1, tzinfo=UTC)

    revision = ContentRevisionResponse.model_validate(
        {
            "id": revision_id,
            "content_item_id": content_id,
            "edition_no": 1,
            "legacy_content_revision": 7,
            "title": "과거 공개 제목",
            "body": "과거 공개 본문",
            "meta_description": None,
            "faq_question": None,
            "faq_answer_summary": None,
            "references_list": [],
            "reference_checks": None,
            "generation_philosophy_id": None,
            "last_reviewed_philosophy_id": None,
            "generated_at": None,
            "reviewed_at": None,
            "reviewed_by": None,
            "source_snapshot": None,
            "generation_provenance": None,
            "source_snapshot_hash": None,
            "source_fingerprint": None,
            "approval_hash": "a" * 64,
            "approval_status": "HISTORICAL_PUBLICATION",
            "approved_at": published_at,
            "approved_by": "LEGACY_AE",
            "created_at": published_at,
        }
    )

    assert revision.id == revision_id
    assert revision.approval_status == "HISTORICAL_PUBLICATION"
    assert revision.source_snapshot is None

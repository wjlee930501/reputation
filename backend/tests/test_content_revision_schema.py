from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.content import (
    ContentRevisionGenerationProvenance,
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

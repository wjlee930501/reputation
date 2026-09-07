from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.workers.content_publication_block_control import (
    _idempotency_key,
    ensure_publication_block_run,
)


def _item(*, philosophy_id: uuid.UUID | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        scheduled_date=date(2026, 8, 19),
        generated_at=datetime(2026, 8, 18, 14, 0, tzinfo=UTC),
        body_updated_at=None,
        content_philosophy_id=philosophy_id,
        image_url="https://cdn.example.test/cover.jpg",
    )


def test_publication_block_key_is_stable_for_same_source_refresh_state() -> None:
    item = _item(philosophy_id=None)

    first = _idempotency_key(item, "ESSENCE_NOT_ALIGNED")
    replay = _idempotency_key(item, "ESSENCE_NOT_ALIGNED")

    assert replay == first


def test_new_approved_essence_starts_a_new_publication_block_episode() -> None:
    item = _item(philosophy_id=None)
    pending_refresh = _idempotency_key(item, "ESSENCE_NOT_ALIGNED")

    item.content_philosophy_id = uuid.uuid4()
    approved_but_misaligned = _idempotency_key(item, "ESSENCE_NOT_ALIGNED")

    assert approved_but_misaligned != pending_refresh


def test_unverified_image_block_routes_to_image_regeneration() -> None:
    item = _item(philosophy_id=uuid.uuid4())
    hospital = SimpleNamespace(id=uuid.uuid4())

    class _MissingRun:
        def scalar_one_or_none(self) -> None:
            return None

    class _DB:
        def __init__(self) -> None:
            self.added = []

        def execute(self, _statement) -> _MissingRun:
            return _MissingRun()

        def add(self, row) -> None:
            self.added.append(row)

    db = _DB()

    run = ensure_publication_block_run(
        db,
        item=item,
        hospital=hospital,
        code="CONTENT_IMAGE_NOT_VERIFIED",
        message="대표 이미지 자동 정책 검사 미완료",
    )

    assert run.operation_type == "REGENERATE_CONTENT_IMAGE"
    assert db.added == [run]

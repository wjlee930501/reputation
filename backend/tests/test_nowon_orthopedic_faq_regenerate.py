"""Coverage for the UUID- and hospital-locked orthopedic FAQ dispatch."""

import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from app.models.content import ContentItem  # noqa: E402
from app.workers import nowon_orthopedic_faq_regenerate as regenerate  # noqa: E402
from app.workers import tasks  # noqa: E402


class _Scalars:
    def __init__(self, items):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _Scalars(self._items)


class _RegenerateDB:
    def __init__(self, items):
        self.items = list(items)

    def execute(self, stmt):
        assert stmt.column_descriptions[0].get("entity") is ContentItem
        query_values = set(stmt.compile().params.values())
        matches = [item for item in self.items if item.id in query_values]
        return _Result(matches[:1])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _item(item_id, hospital_id):
    return SimpleNamespace(id=item_id, hospital_id=hospital_id)


def _run(monkeypatch, items):
    apply_async = Mock()
    monkeypatch.setattr(regenerate, "SyncSessionLocal", lambda: _RegenerateDB(items))
    monkeypatch.setattr(tasks.regenerate_content_item, "apply_async", apply_async)
    result = regenerate.regenerate_nowon_orthopedic_faq()
    return result, apply_async


def test_matching_item_enqueues_exactly_once(monkeypatch):
    result, apply_async = _run(
        monkeypatch,
        [_item(regenerate.ORTHOPEDIC_FAQ_ITEM_ID, regenerate.NOWON_HOSPITAL_ID)],
    )

    assert result == 1
    assert apply_async.call_count == 1
    assert apply_async.call_args.kwargs["args"] == [
        "64882bde-925d-46f9-8c40-7e396c92d9b1"
    ]


def test_missing_item_does_not_enqueue(monkeypatch):
    result, apply_async = _run(monkeypatch, [])

    assert result == 0
    apply_async.assert_not_called()


def test_hospital_mismatch_does_not_enqueue(monkeypatch):
    result, apply_async = _run(
        monkeypatch,
        [_item(regenerate.ORTHOPEDIC_FAQ_ITEM_ID, uuid.uuid4())],
    )

    assert result == 0
    apply_async.assert_not_called()


def test_unrelated_items_are_ignored_and_only_locked_item_enqueues(monkeypatch):
    result, apply_async = _run(
        monkeypatch,
        [
            _item(uuid.UUID("3bc8671e-3333-4333-8333-333333333333"), uuid.uuid4()),
            _item(regenerate.ORTHOPEDIC_FAQ_ITEM_ID, regenerate.NOWON_HOSPITAL_ID),
            _item(uuid.uuid4(), regenerate.NOWON_HOSPITAL_ID),
        ],
    )

    assert result == 1
    assert apply_async.call_count == 1
    assert apply_async.call_args.kwargs["args"] == [
        "64882bde-925d-46f9-8c40-7e396c92d9b1"
    ]

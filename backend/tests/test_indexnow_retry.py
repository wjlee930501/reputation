from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.operations import OperationRun, OperationRunState
from app.services import indexnow
from app.workers import indexnow_retry


class _AsyncExecuteOnly:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)


class _SyncExecuteOnly:
    def __init__(self) -> None:
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_upsert_inside_async_caller_transaction() -> None:
    db = _AsyncExecuteOnly()
    kwargs = {
        "base_url": "https://clinic.example.com",
        "urls": ["https://clinic.example.com/contents/a"],
        "revision": "content-a:7",
    }

    first = await indexnow.enqueue_urls(db, **kwargs)
    second = await indexnow.enqueue_urls(db, **kwargs)

    assert first == second
    assert len(db.statements) == 2
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO NOTHING" in sql


def test_enqueue_sync_uses_revision_and_url_set_for_identity() -> None:
    db = _SyncExecuteOnly()
    base = "https://clinic.example.com"

    first = indexnow.enqueue_urls_sync(
        db, base_url=base, urls=[f"{base}/contents/a"], revision=7
    )
    reordered = indexnow.enqueue_urls_sync(
        db,
        base_url=base,
        urls=[f"{base}/contents/a", f"{base}/contents/a"],
        revision=7,
    )
    changed = indexnow.enqueue_urls_sync(
        db, base_url=base, urls=[f"{base}/contents/a"], revision=8
    )

    assert reordered == first
    assert changed != first


def test_enqueue_rejects_cross_host_or_oversized_intent() -> None:
    db = _SyncExecuteOnly()
    with pytest.raises(ValueError):
        indexnow.enqueue_urls_sync(
            db,
            base_url="https://clinic.example.com",
            urls=["https://other.example.com/a"],
            revision=1,
        )
    with pytest.raises(ValueError):
        indexnow.enqueue_urls_sync(
            db,
            base_url="https://clinic.example.com",
            urls=[
                f"https://clinic.example.com/{value}"
                for value in range(indexnow.MAX_URLS_PER_REQUEST + 1)
            ],
            revision=1,
        )


def test_retry_due_obeys_backoff_and_expired_lease() -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    requested = OperationRun(
        operation_type=indexnow.INDEXNOW_OPERATION_TYPE,
        state=OperationRunState.REQUESTED,
        attempt_count=2,
        heartbeat_at=now - timedelta(minutes=4),
    )
    running = OperationRun(
        operation_type=indexnow.INDEXNOW_OPERATION_TYPE,
        state=OperationRunState.RUNNING,
        attempt_count=1,
        lease_owner="worker-a",
        lease_expires_at=now - timedelta(seconds=1),
    )

    assert indexnow_retry._retry_due(requested, now) is False
    requested.heartbeat_at = now - timedelta(minutes=5)
    assert indexnow_retry._retry_due(requested, now) is True
    assert indexnow_retry._retry_due(running, now) is True


def test_expired_claim_at_attempt_cap_is_terminal_without_another_provider_call(
    monkeypatch,
) -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    run = OperationRun(
        id=uuid4(),
        operation_type=indexnow.INDEXNOW_OPERATION_TYPE,
        state=OperationRunState.RUNNING,
        attempt_count=indexnow_retry._MAX_ATTEMPTS,
        lease_owner="expired-claim",
        lease_expires_at=now - timedelta(seconds=1),
        total_count=1,
        failure_count=0,
        success_count=0,
        skipped_count=0,
        request_payload={
            "schema_version": 1,
            "base_url": "https://clinic.example.com",
            "revision": "7",
            "urls": ["https://clinic.example.com/contents/a"],
        },
        version=5,
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [run]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            return Result()

        def commit(self):
            return None

    monkeypatch.setattr(indexnow_retry, "SyncSessionLocal", Session)

    claimed, terminal = indexnow_retry._claim_due(now, "new-claim")

    assert claimed == []
    assert terminal == 1
    assert run.state == OperationRunState.FAILED
    assert run.safe_error_code == "INDEXNOW_RETRY_EXHAUSTED"
    assert run.lease_owner is None
    assert indexnow_retry._retry_due(run, now + timedelta(days=1)) is False


def test_coalescing_deduplicates_urls_by_canonical_host() -> None:
    base = "https://clinic.example.com"
    first = indexnow_retry.ClaimedIntent(
        uuid4(), "claim-a", base, (f"{base}/", f"{base}/contents"), "1", 1, 2
    )
    second = indexnow_retry.ClaimedIntent(
        uuid4(), "claim-a", base, (f"{base}/contents", f"{base}/contents/a"), "2", 1, 2
    )

    grouped = indexnow_retry._coalesced_groups([first, second])

    assert grouped[base][0] == [f"{base}/", f"{base}/contents", f"{base}/contents/a"]
    assert grouped[base][1] == [first, second]


def test_stale_completion_cannot_finish_a_newer_claim(monkeypatch) -> None:
    run_id = uuid4()
    run = OperationRun(
        id=run_id,
        operation_type=indexnow.INDEXNOW_OPERATION_TYPE,
        state=OperationRunState.RUNNING,
        attempt_count=2,
        lease_owner="same-token",
        lease_expires_at=datetime(2026, 9, 7, 0, 3, tzinfo=UTC),
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload={"revision": "8"},
        version=3,
    )
    stale = indexnow_retry.ClaimedIntent(
        run_id,
        "same-token",
        "https://clinic.example.com",
        ("https://clinic.example.com/contents/a",),
        "7",
        1,
        2,
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [run]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            return Result()

        def commit(self):
            return None

    monkeypatch.setattr(indexnow_retry, "SyncSessionLocal", Session)

    assert indexnow_retry._finish(
        [stale],
        succeeded=True,
        retryable=False,
        now=datetime(2026, 9, 7, 0, 1, tzinfo=UTC),
    ) == (0, 0)
    assert run.state == OperationRunState.RUNNING
    assert run.version == 3


def test_drain_is_bounded_quiet_recovery(monkeypatch) -> None:
    base = "https://clinic.example.com"
    claimed = indexnow_retry.ClaimedIntent(
        uuid4(), "claim-a", base, (f"{base}/contents/a",), "7", 1, 2
    )
    finishes = []

    monkeypatch.setattr(indexnow, "is_configured", lambda: True)
    monkeypatch.setattr(indexnow_retry, "_claim_due", lambda *_args: ([claimed], 0))

    async def submit(_base_url: str, _urls: list[str]) -> indexnow.SubmissionResult:
        return indexnow.SubmissionResult(False, True, "transient_failure")

    monkeypatch.setattr(indexnow_retry, "_submit", submit)
    monkeypatch.setattr(
        indexnow_retry,
        "_finish",
        lambda members, **kwargs: finishes.append((members, kwargs)) or (0, 1),
    )
    monkeypatch.setattr(
        indexnow_retry,
        "current_task",
        SimpleNamespace(request=SimpleNamespace(id="task-a")),
    )

    result = indexnow_retry.drain.run()

    assert result == {"claimed": 1, "submitted": 0, "retrying": 1, "terminal": 0}
    assert finishes[0][0] == [claimed]
    assert finishes[0][1]["succeeded"] is False
    assert finishes[0][1]["retryable"] is True


def test_backfill_writes_bounded_durable_intents_without_provider_calls(monkeypatch) -> None:
    from app.workers import tasks

    hospital = SimpleNamespace(
        id=uuid4(),
        name="테스트 병원",
        slug="test-clinic",
        aeo_domain="clinic.example.com",
        treatments=[],
        site_live=True,
    )
    content_rows = [
        SimpleNamespace(id=uuid4(), content_revision=3),
        SimpleNamespace(id=uuid4(), content_revision=8),
    ]
    urls = [f"https://clinic.example.com/contents/{value}" for value in range(501)]

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.execute_count = 0
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            self.execute_count += 1
            return Result([hospital] if self.execute_count == 1 else content_rows)

        def commit(self):
            self.committed = True

    session = Session()
    queued = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(indexnow, "is_configured", lambda: True)
    monkeypatch.setattr(
        indexnow,
        "hospital_all_urls",
        lambda **_kwargs: ("https://clinic.example.com", urls),
    )
    monkeypatch.setattr(
        indexnow,
        "enqueue_urls_sync",
        lambda _db, **kwargs: queued.append(kwargs),
    )
    monkeypatch.setattr(
        indexnow,
        "submit_urls",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider call is not allowed")),
    )

    result = tasks.backfill_indexnow.run()

    assert result["queued"] == 1
    assert result["total_urls"] == 501
    assert session.committed is True
    assert [len(intent["urls"]) for intent in queued] == [500, 1]
    assert queued[0]["revision"].endswith(":0")
    assert queued[1]["revision"].endswith(":1")

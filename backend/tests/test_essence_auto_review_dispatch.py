"""자동 운영 요약(Essence) 태스크 — 승인·보류 사고 없이 적용하고 첫 일정을 연다(Phase 4 D33)."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.essence_auto_review import (
    EssenceRefreshResult,
    EssenceRefreshStatus,
    EssenceSynthesisDeferred,
)
from app.workers import tasks
from app.workers.dispatch_envelope import expected_purpose, expected_target


class _EssenceTaskDB:
    def __init__(self, hospital):
        self.hospital = hospital

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args):
        return self.hospital


def _run_essence_task(monkeypatch, result, *, revalidated=None, schedules=None):
    hospital = SimpleNamespace(id=result.hospital_id, name="에센스테스트의원", slug="essence-test")
    opened: list[dict] = []

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    async def fake_revalidate(slug, **_kwargs):
        if revalidated is not None:
            revalidated.append(slug)

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _EssenceTaskDB(hospital))
    monkeypatch.setattr(tasks, "refresh_essence_snapshot", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(tasks, "open_ops_incident", fake_open)
    monkeypatch.setattr(tasks, "trigger_hospital_site_revalidate_safe", fake_revalidate)
    monkeypatch.setattr(
        tasks,
        "_ensure_initial_schedule_safe",
        lambda hospital_id, *, trigger: (
            schedules.append((hospital_id, trigger)) if schedules is not None else None
        )
        or "CREATED",
    )
    response = tasks.auto_review_essence_snapshot.run(str(result.hospital_id))
    return response, opened


def test_essence_dispatch_is_purpose_and_hospital_bound() -> None:
    hospital_id = str(uuid.uuid4())

    assert (
        expected_purpose("app.workers.tasks.auto_review_essence_snapshot")
        == "auto-review-essence-snapshot"
    )
    assert (
        expected_target("app.workers.tasks.auto_review_essence_snapshot", [hospital_id])
        == hospital_id
    )
    assert (
        expected_purpose("app.workers.tasks.reconcile_essence_snapshots")
        == "reconcile-essence-snapshots"
    )


def test_reconcile_offset_rotates_across_every_page() -> None:
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(0, timezone.utc)) == 0
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(900, timezone.utc)) == 200
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(1800, timezone.utc)) == 400
    assert tasks._essence_reconcile_offset(450, datetime.fromtimestamp(2700, timezone.utc)) == 0


def test_cost_guard_defers_synthesis_before_the_provider_on_the_essence_budget(monkeypatch) -> None:
    """합성이 야간 생성과 같은 `content` 예산을 쓰면 온보딩이 글을 굶긴다. 보류는 실패가 아니다."""

    categories: list[str] = []

    async def blocked(category, *_args, **_kwargs):
        categories.append(category)
        return type("Decision", (), {"allowed": False, "reason": "limit"})()

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", blocked)
    monkeypatch.setattr(
        tasks,
        "synthesize_philosophy_with_mode",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )

    with pytest.raises(EssenceSynthesisDeferred, match="limit"):
        tasks._cost_guarded_essence_synthesis(object(), [], [])
    assert categories == ["essence"]
    assert not hasattr(tasks, "_cost_guarded_essence_review")


@pytest.mark.parametrize("status", list(EssenceRefreshStatus))
def test_the_automatic_essence_task_never_opens_an_incident(monkeypatch, status) -> None:
    result = EssenceRefreshResult(status=status, hospital_id=uuid.uuid4())
    response, opened = _run_essence_task(monkeypatch, result)
    assert response["status"] == status.value
    assert opened == []


def test_applied_essence_revalidates_a_live_site_and_creates_the_first_schedule(monkeypatch) -> None:
    revalidated: list[str] = []
    schedules: list = []
    result = EssenceRefreshResult(
        EssenceRefreshStatus.APPLIED,
        uuid.uuid4(),
        philosophy_id=uuid.uuid4(),
        trigger="T1_NO_ESSENCE",
        should_revalidate_site=True,
    )

    response, _opened = _run_essence_task(
        monkeypatch, result, revalidated=revalidated, schedules=schedules
    )

    assert revalidated == ["essence-test"]
    assert schedules == [(result.hospital_id, "essence_auto_approval")]
    assert response["initial_schedule"] == "CREATED"
    assert response["trigger"] == "T1_NO_ESSENCE"


@pytest.mark.parametrize(
    "status",
    [
        EssenceRefreshStatus.UP_TO_DATE,
        EssenceRefreshStatus.DEFERRED,
        EssenceRefreshStatus.WAITING_FOR_SOURCES,
    ],
)
def test_other_outcomes_leave_scheduling_to_the_sweep(monkeypatch, status) -> None:
    schedules: list = []
    result = EssenceRefreshResult(status, uuid.uuid4(), previous_philosophy_id=uuid.uuid4())

    response, _opened = _run_essence_task(monkeypatch, result, schedules=schedules)

    assert schedules == []
    assert response["initial_schedule"] is None


def test_unexpected_failure_releases_the_claim_and_retries_without_an_incident(monkeypatch) -> None:
    released: list[dict] = []
    opened: list[dict] = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    async def fake_open(**kwargs):
        opened.append(kwargs)

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _EssenceTaskDB(None))
    monkeypatch.setattr(tasks, "refresh_essence_snapshot", boom)
    monkeypatch.setattr(
        tasks, "release_essence_refresh_claim", lambda _db, **kwargs: released.append(kwargs)
    )
    monkeypatch.setattr(tasks, "open_ops_incident", fake_open)
    monkeypatch.setattr(
        tasks.auto_review_essence_snapshot,
        "retry",
        lambda **kwargs: RuntimeError("retry scheduled"),
    )

    with pytest.raises(RuntimeError, match="retry scheduled"):
        tasks.auto_review_essence_snapshot.run(str(uuid.uuid4()))
    assert released and released[0]["error_code"] == "ESSENCE_REFRESH_FAILED"
    assert opened == []


def test_missing_essence_in_generation_requests_an_immediate_refresh(monkeypatch) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(tasks, "_essence_refresh_requested_at", {})
    monkeypatch.setattr(
        tasks.auto_review_essence_snapshot,
        "apply_async",
        lambda **kwargs: sent.append((kwargs["args"], kwargs["queue"])),
    )
    hospital_id = uuid.uuid4()

    assert tasks._request_essence_refresh(hospital_id) is True
    # 배치가 글마다 불러도 같은 병원은 한 번만 보낸다(claim이 중복 실행도 막는다).
    assert tasks._request_essence_refresh(hospital_id) is False
    assert sent == [([str(hospital_id)], "content")]


def test_schedule_failure_never_undoes_the_approval(monkeypatch) -> None:
    class _Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _Boom())
    assert tasks._ensure_initial_schedule_safe(uuid.uuid4(), trigger="x") == "FAILED"


def test_sweep_offers_only_unscheduled_public_hospitals(monkeypatch) -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]
    statements: list = []

    class _IdsDB:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, stmt):
            statements.append(str(stmt))

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return list(ids)

            return _R()

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _IdsDB())
    outcomes = iter(["CREATED", "ESSENCE_NOT_CURRENT"])
    seen: list = []
    monkeypatch.setattr(
        tasks,
        "_ensure_initial_schedule_safe",
        lambda hospital_id, *, trigger: seen.append((hospital_id, trigger)) or next(outcomes),
    )

    assert tasks._ensure_initial_schedules() == 1
    assert seen == [(ids[0], "reconcile_sweep"), (ids[1], "reconcile_sweep")]
    sql = statements[0]
    assert "content_schedules" in sql and "schedule_set" in sql and "status" in sql

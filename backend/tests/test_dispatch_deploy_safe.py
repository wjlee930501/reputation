"""배포·적체 중의 중복/만료 배달이 실행을 망가뜨리거나 사고를 열지 않는다(2026-10-08).

- 만료·위조된 사본은 실행을 claim하지도, 끝내지도 못한다(검증 뒤에 claim).
- 이미 다른 사본이 가져간 실행의 늦은 사본은 실패가 아니라 Ignore다 — task_failure도 사고도 없다.
- 서명·봉투가 틀린 배달은 그대로 실패하고 사고를 연다.
- 재배달은 언제나 새 task id로 보내고 QUEUED로 표시한다.
- 봉투 수명은 '정확히 같음'이 아니라 상한이다 — 이전 릴리스(3600초) 봉투도 롤아웃 중 유효하다.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore
from celery.signals import task_failure

from app.core import database
from app.core.celery_app import celery_app
from app.models.operations import OperationRunState
from app.workers import (
    autonomous_recovery,
    dispatch_auth,
    dispatch_envelope,
    operation_run_signals,
    task_incident_control,
)

IMAGE_TASK = "app.workers.tasks.generate_content_image"
ISSUED = 1_700_000_000
_ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"

# 워커와 같은 태스크 등록 상태(`include`)로 시험한다.
celery_app.loader.import_default_modules()


def _production(monkeypatch, *, now: int = ISSUED + 1, run=None) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    monkeypatch.setattr(dispatch_auth.time, "time", lambda: now)

    class _RunSession:
        def __enter__(self):
            return SimpleNamespace(
                get=lambda _model, key: run if run is not None and key == run.id else None
            )

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(database, "SyncSessionLocal", _RunSession)


def _image_run(*, task_id: str, state=OperationRunState.RUNNING, lease_owner=None, version=3):
    content_id = str(uuid.uuid4())
    return SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="REGENERATE_CONTENT_IMAGE",
        state=state,
        task_id=task_id,
        lease_owner=lease_owner,
        version=version,
        hospital_id=uuid.uuid4(),
        request_payload={
            "_dispatch": {
                "target_type": "content_item",
                "target_id": content_id,
                "queue": "content",
                "task_args": [content_id],
            }
        },
    )


def _stamped(run, task_id: str, *, now: int = ISSUED) -> dict[str, str]:
    target = run.request_payload["_dispatch"]["target_id"]
    return dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id=task_id,
        args=[target],
        kwargs={},
        retries=0,
        headers={"operation_run_id": str(run.id)},
        now=now,
    )


def _task(headers, task_id: str):
    request = SimpleNamespace(id=task_id, retries=0, headers=headers, delivery_info={})
    return SimpleNamespace(name=IMAGE_TASK, request=request)


@pytest.fixture
def claims(monkeypatch):
    """실제 claim SQL 대신 호출만 기록한다. 반환값은 테스트가 정한다."""

    calls: list[tuple[uuid.UUID, str]] = []
    result = {"version": None}

    def _claim(run_id, worker_id, _now, *, redelivered):
        del redelivered
        calls.append((run_id, worker_id))
        return result["version"]

    monkeypatch.setattr(operation_run_signals, "_claim", _claim)
    return SimpleNamespace(calls=calls, result=result)


# ── A. 검증 뒤에 claim ─────────────────────────────────────────────────────────


def test_an_expired_copy_cannot_claim_the_run(monkeypatch, claims) -> None:
    run = _image_run(task_id="copy")
    _production(monkeypatch, now=ISSUED + dispatch_envelope.DISPATCH_TTL_SECONDS + 1, run=run)
    headers = _stamped(run, "copy")

    with pytest.raises(dispatch_auth.DispatchAuthorizationError, match="expired"):
        dispatch_auth.AuthenticatedTask.before_start(
            _task(headers, "copy"), "copy", (run.request_payload["_dispatch"]["target_id"],), {}
        )

    assert claims.calls == []


def test_a_forged_copy_cannot_claim_the_run(monkeypatch, claims) -> None:
    run = _image_run(task_id="copy")
    _production(monkeypatch, run=run)
    headers = {**_stamped(run, "copy"), dispatch_envelope.SIGNATURE_HEADER: "0" * 64}

    with pytest.raises(dispatch_auth.DispatchAuthorizationError, match="signature"):
        dispatch_auth.AuthenticatedTask.before_start(
            _task(headers, "copy"), "copy", (run.request_payload["_dispatch"]["target_id"],), {}
        )

    assert claims.calls == []


def test_a_valid_copy_claims_then_is_authorized(monkeypatch, claims) -> None:
    run = _image_run(task_id="copy", lease_owner="copy", version=3)
    claims.result["version"] = 3
    _production(monkeypatch, run=run)
    task = _task(_stamped(run, "copy"), "copy")

    dispatch_auth.AuthenticatedTask.before_start(
        task, "copy", (run.request_payload["_dispatch"]["target_id"],), {}
    )

    assert claims.calls == [(run.id, "copy")]
    assert task.request.operation_run_claim_version == 3


# ── C. 늦은 사본은 Ignore, 진짜 위조는 실패 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("run_kwargs", "reason"),
    [
        ({"task_id": "newer-copy", "lease_owner": "newer-copy"}, "superseded"),
        (
            {"task_id": "copy", "state": OperationRunState.SUCCEEDED, "lease_owner": None},
            "already_terminal",
        ),
        ({"task_id": "copy", "lease_owner": "copy"}, "duplicate_delivery"),
    ],
)
def test_a_stale_duplicate_is_ignored_with_a_reason(
    monkeypatch, claims, caplog, run_kwargs, reason
) -> None:
    run = _image_run(**run_kwargs)
    _production(monkeypatch, run=run)
    caplog.set_level(logging.INFO, logger="app.workers.dispatch_auth")

    with pytest.raises(Ignore):
        dispatch_auth.AuthenticatedTask.before_start(
            _task(_stamped(run, "copy"), "copy"),
            "copy",
            (run.request_payload["_dispatch"]["target_id"],),
            {},
        )

    skipped = [r for r in caplog.records if "dispatch_skipped" in r.getMessage()]
    assert len(skipped) == 1
    assert skipped[0].levelno == logging.INFO
    assert f"reason={reason}" in skipped[0].getMessage()


def test_a_run_whose_target_differs_is_still_a_hard_failure(monkeypatch, claims) -> None:
    run = _image_run(task_id="copy", lease_owner="copy")
    claims.result["version"] = 3
    _production(monkeypatch, run=run)
    other = str(uuid.uuid4())
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id="copy",
        args=[other],
        kwargs={},
        retries=0,
        headers={"operation_run_id": str(run.id)},
        now=ISSUED,
    )

    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.AuthenticatedTask.before_start(_task(headers, "copy"), "copy", (other,), {})


def _apply_through_celery(monkeypatch, run, headers, task_id):
    """Celery의 실제 tracer로 실행해 task_failure와 사고 투영이 일어나는지 본다."""

    failures: list[str] = []
    projected: list[str] = []
    finished: list[object] = []

    def _on_failure(sender=None, task_id=None, **_kwargs):
        failures.append(str(task_id))

    monkeypatch.setattr(
        task_incident_control,
        "record_task_failure",
        lambda _task, worker_task_id: projected.append(str(worker_task_id)) or True,
    )
    monkeypatch.setattr(
        operation_run_signals,
        "_execute_safely",
        lambda statement, *_args: finished.append(statement),
    )
    task_failure.connect(_on_failure, weak=False)
    try:
        result = celery_app.tasks[IMAGE_TASK].apply(
            args=[run.request_payload["_dispatch"]["target_id"]],
            task_id=task_id,
            headers=headers,
        )
    finally:
        task_failure.disconnect(_on_failure)
    return result, failures, projected, finished


def test_a_stale_duplicate_fires_no_task_failure_and_opens_no_incident(monkeypatch, claims) -> None:
    run = _image_run(task_id="newer-copy", lease_owner="newer-copy")
    _production(monkeypatch, run=run)

    result, failures, projected, finished = _apply_through_celery(
        monkeypatch, run, _stamped(run, "copy", now=ISSUED + 1), "copy"
    )

    assert result.state == "IGNORED"
    assert failures == []
    assert projected == []
    assert finished == []


def test_a_bad_signature_still_fails_and_opens_an_incident(monkeypatch, claims) -> None:
    run = _image_run(task_id="copy", lease_owner="copy")
    _production(monkeypatch, run=run)
    headers = {
        **_stamped(run, "copy", now=ISSUED + 1),
        dispatch_envelope.SIGNATURE_HEADER: "f" * 64,
    }

    result, failures, projected, finished = _apply_through_celery(monkeypatch, run, headers, "copy")

    assert result.state == "FAILURE"
    assert failures == ["copy"]
    assert projected == ["copy"]
    # 위조된 사본은 claim하지 못했으므로 실행을 FAILED로 끝내지도 못한다.
    assert claims.calls == []
    assert finished == []


# ── D. 시계 ──────────────────────────────────────────────────────────────────


def _stamp_with_lifetime(monkeypatch, lifetime: int, target: str) -> dict[str, str]:
    monkeypatch.setattr(dispatch_envelope, "DISPATCH_TTL_SECONDS", lifetime)
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id="t",
        args=[target],
        kwargs={},
        retries=0,
        headers={},
        now=ISSUED,
    )
    monkeypatch.setattr(dispatch_envelope, "DISPATCH_TTL_SECONDS", 3600)
    return headers


@pytest.mark.parametrize(
    "lifetime",
    # 3600초: #226 이전 릴리스와 이번 릴리스가 서명하는 수명. 상한: 다음 릴리스가 서명할 수명.
    [3600, dispatch_envelope.DISPATCH_MAX_LIFETIME_SECONDS],
)
def test_an_envelope_lifetime_within_the_bound_validates(monkeypatch, lifetime) -> None:
    _production(monkeypatch, now=ISSUED + 10)
    target = str(uuid.uuid4())
    headers = _stamp_with_lifetime(monkeypatch, lifetime, target)

    dispatch_auth.validate_task_dispatch(
        task_name=IMAGE_TASK, task_id="t", args=[target], kwargs={}, retries=0, headers=headers
    )


@pytest.mark.parametrize("lifetime", [0, dispatch_envelope.DISPATCH_MAX_LIFETIME_SECONDS + 1])
def test_an_envelope_lifetime_outside_the_bound_is_rejected(monkeypatch, lifetime) -> None:
    _production(monkeypatch, now=ISSUED)
    target = str(uuid.uuid4())
    headers = _stamp_with_lifetime(monkeypatch, lifetime, target)

    with pytest.raises(
        dispatch_auth.DispatchAuthorizationError, match="invalid authenticated dispatch lifetime"
    ):
        dispatch_auth.validate_task_dispatch(
            task_name=IMAGE_TASK,
            task_id="t",
            args=[target],
            kwargs={},
            retries=0,
            headers=headers,
        )


def test_this_release_signs_the_lifetime_pre_226_workers_accept() -> None:
    """#226 이전 워커는 수명이 정확히 3600초인 봉투만 받는다 — 겹침 구간에 그 워커가 소비해도 안전하다."""

    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK, task_id="t", args=["x"], kwargs={}, retries=0, headers={}, now=ISSUED
    )
    issued = int(headers[dispatch_envelope.ISSUED_HEADER])
    assert int(headers[dispatch_envelope.EXPIRES_HEADER]) - issued == 3600


def test_clocks_are_ordered_for_redelivery_and_leases() -> None:
    longest_hard_limit = max(
        int(getattr(task, "time_limit", None) or celery_app.conf.task_time_limit)
        for name, task in celery_app.tasks.items()
        if name.startswith("app.workers.")
    )
    visibility = celery_app.conf.broker_transport_options["visibility_timeout"]
    assert celery_app.conf.broker_transport_options["queue_order_strategy"] == "priority"
    assert visibility > longest_hard_limit
    assert dispatch_envelope.DISPATCH_MAX_LIFETIME_SECONDS > visibility
    assert dispatch_envelope.DISPATCH_TTL_SECONDS <= dispatch_envelope.DISPATCH_MAX_LIFETIME_SECONDS
    # 살아 있는 실행을 다른 사본이 가로채지 않고(> hard limit), 강제 종료된 실행은
    # 재배달 사본이 이어받을 수 있다(<= visibility timeout).
    assert longest_hard_limit < operation_run_signals._LEASE_SECONDS <= visibility


# ── E. 배포 종료는 cold shutdown ──────────────────────────────────────────────


def test_the_worker_remaps_sigterm_to_a_cold_shutdown() -> None:
    script = _ENTRYPOINT.read_text()
    worker_branch = re.search(r"\n  worker\)\n(.*?)\n    ;;", script, re.DOTALL)
    assert worker_branch, "docker-entrypoint.sh에 worker 분기가 없다"
    body = worker_branch.group(1)
    assert "export REMAP_SIGTERM=SIGQUIT" in body
    assert body.index("REMAP_SIGTERM") < body.index("exec celery")


# ── B. 재배달은 새 task id ───────────────────────────────────────────────────


def test_redispatch_always_sends_a_new_task_id_and_marks_queued(monkeypatch) -> None:
    now = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.QUEUED,
        hospital_id=hospital_id,
        task_id="original-copy",
        request_payload={
            "_dispatch": {
                "target_type": "hospital",
                "target_id": str(hospital_id),
                "queue": "reports",
                "task_args": [str(hospital_id)],
            }
        },
        requested_at=now - timedelta(hours=4),
        queued_at=now - timedelta(hours=4),
        completed_at=None,
        heartbeat_at=None,
        lease_owner=None,
        lease_expires_at=None,
        safe_error_code=None,
        safe_error_message=None,
        version=4,
    )
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: sent.append({"name": name, **kwargs}),
    )

    assert autonomous_recovery._redispatch_operation_run(SimpleNamespace(), run, now)

    assert run.task_id != "original-copy"
    assert uuid.UUID(run.task_id)
    assert sent[0]["task_id"] == run.task_id
    assert run.state == OperationRunState.QUEUED
    assert run.queued_at == now
    assert run.version == 5


def test_a_queued_run_waits_hours_before_being_called_lost() -> None:
    now = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)

    def _queued(minutes):
        return SimpleNamespace(
            state=OperationRunState.QUEUED,
            operation_type="GENERATE_CONTENT_ITEM",
            queued_at=now - timedelta(minutes=minutes),
            requested_at=now - timedelta(minutes=minutes),
        )

    # 관측된 최악의 content 적체는 약 70분이다.
    assert not autonomous_recovery._operation_redispatch_is_due(_queued(70), now)
    assert not autonomous_recovery._operation_redispatch_is_due(_queued(179), now)
    assert autonomous_recovery._operation_redispatch_is_due(_queued(181), now)


# ── B. 배포 지점은 publish 직후 QUEUED로 표시한다 ───────────────────────────────


def _fan_out(monkeypatch, *, publish_fails: bool):
    from app.workers import tasks

    run = SimpleNamespace(id=uuid.uuid4())
    marked: list[uuid.UUID] = []
    recorded: list[object] = []

    def _send(*_args, **_kwargs):
        if publish_fails:
            raise ConnectionError("broker down")

    monkeypatch.setattr(tasks, "create_dispatched_item_run", lambda *_a, **_k: run)
    monkeypatch.setattr(tasks.celery_app, "send_task", _send)
    monkeypatch.setattr(
        tasks, "mark_operation_run_queued", lambda _db, run_id, _at: marked.append(run_id)
    )
    item = SimpleNamespace(
        id=uuid.uuid4(), hospital_id=uuid.uuid4(), generation_claim_token=uuid.uuid4()
    )
    recorder = SimpleNamespace(
        run=SimpleNamespace(id=uuid.uuid4(), attempt_count=1),
        record=lambda item_id, state: recorded.append((item_id, state)),
    )
    dispatched = tasks._dispatch_generation_item(SimpleNamespace(), recorder, item, notify=None)
    return run, dispatched, marked


def test_the_generation_fan_out_marks_the_run_queued_after_publish(monkeypatch) -> None:
    run, dispatched, marked = _fan_out(monkeypatch, publish_fails=False)

    assert dispatched is True
    assert marked == [run.id]


def test_a_failed_publish_leaves_the_run_requested_for_recovery(monkeypatch) -> None:
    _run, dispatched, marked = _fan_out(monkeypatch, publish_fails=True)

    assert dispatched is False
    assert marked == []


def test_the_queued_mark_never_fails_the_publish(monkeypatch) -> None:
    from app.workers import generation_run_control

    rolled_back: list[bool] = []

    def _broken(_statement):
        raise RuntimeError("database unavailable")

    db = SimpleNamespace(
        execute=_broken, commit=lambda: None, rollback=lambda: rolled_back.append(True)
    )

    assert not generation_run_control.mark_operation_run_queued(
        db, uuid.uuid4(), datetime(2026, 10, 8, tzinfo=UTC)
    )
    assert rolled_back == [True]


# ── 긴 countdown 금지: 미룬 실행은 DB 시각으로 다시 보낸다 ─────────────────────


def test_no_dispatch_site_holds_a_message_longer_than_the_bound() -> None:
    import inspect

    from app.workers import tasks

    source = inspect.getsource(tasks)
    literal = [int(value) for value in re.findall(r"countdown=(\d+)\b", source)]
    assert literal and max(literal) <= dispatch_envelope.MAX_DISPATCH_COUNTDOWN_SECONDS
    assert dispatch_envelope.MAX_DISPATCH_COUNTDOWN_SECONDS == 15 * 60
    assert (
        tasks.SOV_CONTINUATION_COUNTDOWN_SECONDS <= dispatch_envelope.MAX_DISPATCH_COUNTDOWN_SECONDS
    )
    # 다음 비용 창까지(최대 약 24시간) countdown으로 기다리던 V0 경로는 없다.
    assert "countdown=_seconds_until_next_kst_cost_window" not in source
    v0 = inspect.getsource(tasks.trigger_v0_report)
    cost_branch = v0[v0.index("except V0CostDeferred") : v0.index("except V0MeasurementResumable")]
    assert "_defer_v0_until_cost_window(" in cost_branch
    assert "defer_operation_run(" in inspect.getsource(tasks._defer_v0_until_cost_window)


def test_a_deferred_run_is_not_stuck_before_its_time() -> None:
    now = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)

    def _deferred(not_before):
        return SimpleNamespace(
            state=OperationRunState.QUEUED,
            operation_type="TRIGGER_V0_REPORT",
            queued_at=now - timedelta(hours=20),
            requested_at=now - timedelta(hours=20),
            not_before_at=not_before,
        )

    # QUEUED 유실 판정 유예(3시간)를 한참 넘겨도 정한 시각 전이면 보내지 않는다.
    assert not autonomous_recovery._operation_redispatch_is_due(
        _deferred(now + timedelta(minutes=1)), now
    )
    assert autonomous_recovery._operation_redispatch_is_due(_deferred(now), now)


# ── V0 비용 보류의 저장 실패는 실행을 잃지 않는다 ───────────────────────────────


class _NoSession:
    def __enter__(self):
        return SimpleNamespace()

    def __exit__(self, *_exc):
        return False


class _Retry(Exception):
    def __init__(self, **kwargs):
        super().__init__("retry")
        self.kwargs = kwargs


def _v0_task(*, claimed: bool):
    request = SimpleNamespace(
        id="v0-copy",
        headers={"operation_run_id": str(uuid.uuid4())},
        operation_run_claim_version=3 if claimed else None,
        kwargs={},
    )

    def _retry(**kwargs):
        return _Retry(**kwargs)

    return SimpleNamespace(request=request, retry=_retry)


def test_a_failed_deferral_write_retries_within_the_bound(monkeypatch) -> None:
    from app.workers import tasks

    def _broken(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(tasks, "SyncSessionLocal", _NoSession)
    monkeypatch.setattr(tasks, "defer_operation_run", _broken)

    with pytest.raises(_Retry) as raised:
        tasks._defer_v0_until_cost_window(_v0_task(claimed=True), RuntimeError("cost"), 0)

    assert raised.value.kwargs["countdown"] == dispatch_envelope.MAX_DISPATCH_COUNTDOWN_SECONDS


def test_a_recorded_deferral_returns_without_a_long_countdown(monkeypatch) -> None:
    from app.workers import tasks

    deferred: list[datetime] = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", _NoSession)
    monkeypatch.setattr(
        tasks,
        "defer_operation_run",
        lambda _db, _task, not_before: deferred.append(not_before) or True,
    )

    result = tasks._defer_v0_until_cost_window(_v0_task(claimed=True), RuntimeError("cost"), 0)

    assert result["status"] == "cost_deferred"
    assert len(deferred) == 1 and deferred[0] > datetime.now(UTC)


def test_a_run_less_v0_delivery_retries_within_the_bound(monkeypatch) -> None:
    from app.workers import tasks

    task = _v0_task(claimed=False)

    with pytest.raises(_Retry) as raised:
        tasks._defer_v0_until_cost_window(task, RuntimeError("cost"), 0)

    assert raised.value.kwargs["countdown"] == dispatch_envelope.MAX_DISPATCH_COUNTDOWN_SECONDS


# ── claim을 확인하지 못한 배달은 중복이 아니라 되돌린다 ─────────────────────────


def test_a_claim_database_error_requeues_instead_of_ignoring(monkeypatch, caplog) -> None:
    from celery.exceptions import Reject
    from sqlalchemy.exc import OperationalError

    run = _image_run(task_id="copy", lease_owner="copy")
    _production(monkeypatch, run=run)
    slept: list[float] = []
    monkeypatch.setattr(dispatch_auth, "CLAIM_UNAVAILABLE_REQUEUE_DELAY_SECONDS", 0)
    monkeypatch.setattr(dispatch_auth.time, "sleep", slept.append)
    caplog.set_level(logging.WARNING, logger="app.workers.dispatch_auth")

    def _down(*_args, **_kwargs):
        raise OperationalError("UPDATE operation_runs", {}, Exception("connection lost"))

    monkeypatch.setattr(operation_run_signals, "_claim", _down)
    task = _task(_stamped(run, "copy"), "copy")
    task.request.delivery_info = {"redelivered": True}

    with pytest.raises(Reject) as raised:
        dispatch_auth.AuthenticatedTask.before_start(
            task, "copy", (run.request_payload["_dispatch"]["target_id"],), {}
        )

    assert raised.value.requeue is True
    assert getattr(task.request, "operation_run_claim_version", None) is None
    # DB 장애 동안 즉시 재배달이 쉼 없이 돌지 않도록, 되돌리기 전에 상한 있는 시간만큼 쉰다.
    assert slept == [0]
    requeued = [r for r in caplog.records if "dispatch_requeued" in r.getMessage()]
    assert len(requeued) == 1
    assert requeued[0].levelno == logging.WARNING
    assert "task_id=copy" in requeued[0].getMessage()
    assert "reason=claim_unavailable" in requeued[0].getMessage()
    assert "error=OperationalError" in requeued[0].getMessage()


def test_the_requeue_pause_is_short_and_bounded() -> None:
    assert 0 < dispatch_auth.CLAIM_UNAVAILABLE_REQUEUE_DELAY_SECONDS <= 5


# ── 미룬 실행은 복구로 보이지 않는다 ──────────────────────────────────────────


def test_a_deferred_run_does_not_recover_its_incident(monkeypatch) -> None:
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        state=OperationRunState.QUEUED,
        not_before_at=datetime(2026, 10, 9, tzinfo=UTC),
        operation_type="TRIGGER_V0_REPORT",
    )

    class _Session:
        def __enter__(self):
            return SimpleNamespace(scalar=lambda _statement: run)

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(task_incident_control, "SyncSessionLocal", _Session)
    monkeypatch.setattr(
        task_incident_control,
        "_recoverable_incident",
        lambda *_a: (_ for _ in ()).throw(AssertionError("a deferral is not a recovery")),
    )
    task = SimpleNamespace(request=SimpleNamespace(headers={"operation_run_id": str(run_id)}))

    assert task_incident_control.record_task_success(task, "v0-copy") is False

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

    monkeypatch.setattr(operation_run_signals, "_claim_safely", _claim)
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


def test_an_envelope_signed_by_the_previous_release_ttl_still_validates(monkeypatch) -> None:
    _production(monkeypatch, now=ISSUED + 10)
    monkeypatch.setattr(dispatch_envelope, "DISPATCH_TTL_SECONDS", 3600)
    target = str(uuid.uuid4())
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id="t",
        args=[target],
        kwargs={},
        retries=0,
        headers={},
        now=ISSUED,
    )
    monkeypatch.undo()
    _production(monkeypatch, now=ISSUED + 10)

    dispatch_auth.validate_task_dispatch(
        task_name=IMAGE_TASK, task_id="t", args=[target], kwargs={}, retries=0, headers=headers
    )


@pytest.mark.parametrize("lifetime", [0, dispatch_envelope.DISPATCH_TTL_SECONDS + 1])
def test_an_envelope_lifetime_outside_the_bound_is_rejected(monkeypatch, lifetime) -> None:
    _production(monkeypatch, now=ISSUED)
    monkeypatch.setattr(dispatch_envelope, "DISPATCH_TTL_SECONDS", lifetime)
    target = str(uuid.uuid4())
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id="t",
        args=[target],
        kwargs={},
        retries=0,
        headers={},
        now=ISSUED,
    )
    monkeypatch.undo()
    _production(monkeypatch, now=ISSUED)

    with pytest.raises(dispatch_auth.DispatchAuthorizationError, match="lifetime"):
        dispatch_auth.validate_task_dispatch(
            task_name=IMAGE_TASK,
            task_id="t",
            args=[target],
            kwargs={},
            retries=0,
            headers=headers,
        )


def test_clocks_are_ordered_so_a_redelivered_message_is_never_expired() -> None:
    longest_hard_limit = max(
        int(getattr(task, "time_limit", None) or celery_app.conf.task_time_limit)
        for name, task in celery_app.tasks.items()
        if name.startswith("app.workers.")
    )
    visibility = celery_app.conf.broker_transport_options["visibility_timeout"]
    assert celery_app.conf.broker_transport_options["queue_order_strategy"] == "priority"
    assert visibility > longest_hard_limit
    assert dispatch_envelope.DISPATCH_TTL_SECONDS > visibility
    assert dispatch_auth.RELEASE_HANDOFF_GRACE_SECONDS == dispatch_envelope.DISPATCH_TTL_SECONDS
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

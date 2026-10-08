"""2026-10-08 사고의 재발 방지 계약.

워커가 "Connection to broker lost"를 남긴 뒤 4시간 넘게 로그·실행 없이 살아 있었다. 그동안 매분
주기 작업 ~1,458건이 쌓였다가 새 워커에서 만료된 봉투로 하나씩 ERROR가 됐다.

- Redis 소켓은 시간 제한·keepalive·health check를 갖는다(브로커·결과 백엔드·RedBeat).
- consumer heartbeat가 끊긴 인스턴스는 /live가 실패해 Cloud Run이 재시작한다.
- 실행 기록 없는 주기 작업의 만료 봉투는 WARNING 한 줄과 Ignore — 사고도 task_failure도 없다.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore
from celery.schedules import crontab
from celery.schedules import schedule as celery_schedule
from celery.signals import heartbeat_sent, task_failure
from redis.connection import parse_url

from app.core import celery_app as celery_module
from app.core.celery_app import celery_app
from app.workers import (
    dispatch_auth,
    dispatch_envelope,
    health_server,
    task_incident_control,
    worker_liveness,
)

celery_app.loader.import_default_modules()

_ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
DRAIN_TASK = "app.workers.indexnow_retry.drain"
ISSUED = 1_700_000_000


# ── A. Redis 소켓 설정 ─────────────────────────────────────────────────────────


def test_broker_transport_options_bound_every_socket_wait() -> None:
    options = celery_app.conf.broker_transport_options
    assert {key: value for key, value in options.items() if key != "socket_keepalive_options"} == {
        "queue_order_strategy": "priority",
        "visibility_timeout": 7200,
        "socket_timeout": 30,
        "socket_connect_timeout": 10,
        "socket_keepalive": True,
        "retry_on_timeout": True,
        "health_check_interval": 25,
    }
    keepalive = options["socket_keepalive_options"]
    assert keepalive, "TCP keepalive 세부값이 비었다 — 커널 기본(2시간)으로 돌아간다"
    assert set(keepalive.values()) <= {60, 10, 3}


def test_every_broker_transport_option_is_a_key_kombu_reads() -> None:
    """kombu는 모르는 키를 조용히 버린다 — 오타 하나가 보강 전체를 무효로 만든다."""
    from kombu.transport.redis import Channel

    unknown = set(celery_app.conf.broker_transport_options) - set(
        Channel.from_transport_options
    )
    assert unknown == set()


def test_broker_reconnect_policy_is_explicit() -> None:
    assert celery_app.conf.broker_connection_retry is True
    assert celery_app.conf.broker_connection_retry_on_startup is True
    # 상한을 남겨 재연결이 끝내 안 되면 프로세스가 끝나고 Cloud Run이 새로 띄운다.
    assert celery_app.conf.broker_connection_max_retries == 100


def test_redis_result_backend_uses_the_same_socket_bounds() -> None:
    from celery.backends.redis import RedisBackend

    backend = RedisBackend(app=celery_app, url="redis://example.invalid:6379/0")
    params = backend.connparams
    assert params["socket_timeout"] == 30.0
    assert params["socket_connect_timeout"] == 10.0
    assert params["socket_keepalive"] is True
    assert params["retry_on_timeout"] is True
    assert params["health_check_interval"] == 25


def test_redbeat_url_carries_socket_bounds_redis_py_parses() -> None:
    parsed = parse_url(celery_app.conf.redbeat_redis_url)
    assert parsed["socket_timeout"] == 30.0
    assert parsed["socket_connect_timeout"] == 10.0
    assert parsed["socket_keepalive"] is True
    assert parsed["retry_on_timeout"] is True
    assert parsed["health_check_interval"] == 25


def test_redbeat_url_keeps_existing_query_values() -> None:
    url = celery_module._redis_url_with_socket_options(
        "rediss://:pw@cache.internal:6378/2?socket_timeout=5&ssl_cert_reqs=required"
    )
    parsed = parse_url(url)
    assert parsed["host"] == "cache.internal"
    assert parsed["port"] == 6378
    assert parsed["db"] == 2
    assert parsed["socket_timeout"] == 5.0
    assert "ssl_cert_reqs=required" in url
    assert parsed["health_check_interval"] == 25


# ── B. consumer heartbeat와 /live ──────────────────────────────────────────────


def test_the_consumer_heartbeat_signal_touches_the_liveness_file() -> None:
    receivers = [receiver for _key, receiver in heartbeat_sent.receivers]
    assert worker_liveness.touch_heartbeat in receivers


def test_touch_heartbeat_writes_and_never_raises(tmp_path) -> None:
    path = tmp_path / "hb"
    worker_liveness.touch_heartbeat(sender=object(), path=path)
    assert path.exists()
    worker_liveness.touch_heartbeat(sender=object(), path=tmp_path / "missing" / "hb")


def _write_heartbeat(path: Path, mtime: float) -> None:
    path.touch()
    os.utime(path, (mtime, mtime))


@pytest.mark.parametrize(
    ("heartbeat_age", "uptime", "alive"),
    [
        (5, 3_600, True),  # 정상 운영
        (600, 3_600, True),  # 경계값은 통과
        (601, 3_600, False),  # 재연결에서 멈춘 consumer
        (None, 120, True),  # 기동 중 — 첫 heartbeat 전
        (None, 300, True),  # 유예 경계
        (None, 301, False),  # 기동 후 한 번도 브로커에 붙지 못했다
        (5_000, 60, True),  # 이전 컨테이너의 흔적 같은 낡은 파일도 기동 유예 안에서는 통과
    ],
)
def test_consumer_alive_matrix(tmp_path, heartbeat_age, uptime, alive) -> None:
    now = 2_000_000_000.0
    path = tmp_path / "hb"
    if heartbeat_age is not None:
        _write_heartbeat(path, now - heartbeat_age)
    assert (
        worker_liveness.consumer_alive(
            started_at=now - uptime,
            stale_seconds=600,
            startup_grace_seconds=300,
            path=path,
            now=now,
        )
        is alive
    )


def test_live_checks_the_heartbeat_only_in_worker_mode(monkeypatch, caplog) -> None:
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server.worker_liveness, "consumer_alive", lambda **_k: False)

    monkeypatch.setattr(health_server, "_CHECK_WORKER_HEARTBEAT", False)
    assert health_server.is_live() is True  # Beat에는 consumer가 없다

    monkeypatch.setattr(health_server, "_CHECK_WORKER_HEARTBEAT", True)
    with caplog.at_level("WARNING"):
        assert health_server.is_live() is False
    assert "worker_liveness_failed reason=consumer_heartbeat_stale" in caplog.text


def test_live_fails_when_the_celery_parent_is_gone(monkeypatch) -> None:
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: False)
    monkeypatch.setattr(health_server, "_CHECK_WORKER_HEARTBEAT", True)
    monkeypatch.setattr(health_server.worker_liveness, "consumer_alive", lambda **_k: True)
    assert health_server.is_live() is False


def test_live_uses_the_configured_thresholds(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server, "_CHECK_WORKER_HEARTBEAT", True)
    monkeypatch.setattr(health_server.settings, "WORKER_LIVENESS_STALE_SECONDS", 900)
    monkeypatch.setattr(health_server.settings, "WORKER_LIVENESS_STARTUP_GRACE_SECONDS", 120)
    monkeypatch.setattr(
        health_server.worker_liveness,
        "consumer_alive",
        lambda **kwargs: seen.update(kwargs) or True,
    )
    assert health_server.is_live() is True
    assert seen["stale_seconds"] == 900
    assert seen["startup_grace_seconds"] == 120


def test_default_thresholds_tolerate_reconnects_but_not_hours() -> None:
    from app.core.config import Settings

    stale = Settings.model_fields["WORKER_LIVENESS_STALE_SECONDS"].default
    grace = Settings.model_fields["WORKER_LIVENESS_STARTUP_GRACE_SECONDS"].default
    assert 600 <= stale <= 900
    assert 120 <= grace <= stale


def _service_branch(name: str) -> str:
    text = _ENTRYPOINT.read_text()
    match = re.search(rf"^  {name}\)\n(.*?)^    ;;", text, re.MULTILINE | re.DOTALL)
    assert match, f"docker-entrypoint.sh에 {name} 분기가 없다"
    return match.group(1)


def test_only_the_worker_health_server_checks_the_consumer_heartbeat() -> None:
    worker = _service_branch("worker")
    beat = _service_branch("beat")
    assert "python -m app.workers.health_server --worker-heartbeat &" in worker
    assert worker.index("--worker-heartbeat") < worker.index("exec celery")
    assert "--worker-heartbeat" not in beat


def test_health_server_flag_parsing(monkeypatch) -> None:
    started: list[bool] = []

    class _Server:
        def __init__(self, *_args):
            started.append(health_server._CHECK_WORKER_HEARTBEAT)

        def serve_forever(self):
            return None

    monkeypatch.setattr(health_server, "HTTPServer", _Server)
    monkeypatch.setattr(health_server, "_CHECK_WORKER_HEARTBEAT", False)
    health_server.main(["--worker-heartbeat"])
    health_server.main([])
    assert started == [True, False]


# ── C. 만료된 주기 작업 봉투 ──────────────────────────────────────────────────


def _production(monkeypatch, *, now: int) -> None:
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    monkeypatch.setattr(dispatch_auth.time, "time", lambda: now)


def _drain_headers(*, purpose: str = "drain-indexnow-retries", extra=None) -> dict[str, str]:
    headers = {**dispatch_envelope.build_dispatch_headers(purpose), **(extra or {})}
    return dispatch_auth.stamp_dispatch_headers(
        task_name=DRAIN_TASK, task_id="late", args=[], kwargs={}, retries=0, headers=headers,
        now=ISSUED,
    )


_EXPIRED = ISSUED + dispatch_envelope.DISPATCH_TTL_SECONDS + 1


def _apply(monkeypatch, headers):
    failures: list[str] = []
    projected: list[str] = []

    def _on_failure(sender=None, task_id=None, **_kwargs):
        failures.append(str(task_id))

    monkeypatch.setattr(
        task_incident_control,
        "record_task_failure",
        lambda _task, worker_task_id: projected.append(str(worker_task_id)) or True,
    )
    task_failure.connect(_on_failure, weak=False)
    try:
        result = celery_app.tasks[DRAIN_TASK].apply(task_id="late", headers=headers)
    finally:
        task_failure.disconnect(_on_failure)
    return result, failures, projected


def test_an_expired_periodic_drain_is_dropped_with_one_warning(monkeypatch, caplog) -> None:
    _production(monkeypatch, now=_EXPIRED)

    with caplog.at_level("WARNING", logger=dispatch_auth.logger.name):
        result, failures, projected = _apply(monkeypatch, _drain_headers())

    assert result.state == "IGNORED"
    assert failures == []
    assert projected == []  # record_task_failure에 닿지 않는다
    dropped = [r for r in caplog.records if "dispatch_expired_dropped" in r.getMessage()]
    assert len(dropped) == 1
    assert dropped[0].levelname == "WARNING"
    assert f"task_name={DRAIN_TASK}" in dropped[0].getMessage()
    assert f"age_seconds={_EXPIRED - ISSUED}" in dropped[0].getMessage()


@pytest.mark.parametrize(
    "task_name",
    [
        "app.workers.provider_usage_recovery.drain",
        "app.workers.notification_tasks.dispatch_notification_outbox",
        "app.workers.autonomous_recovery.reconcile",
    ],
)
def test_every_minute_drains_are_recognized_as_run_less_periodic(task_name) -> None:
    headers = dispatch_auth.stamp_dispatch_headers(
        task_name=task_name, task_id="t", args=[], kwargs={}, retries=0,
        headers={}, now=ISSUED,
    )
    assert dispatch_auth._is_run_less_periodic_dispatch(task_name, headers) is True


def test_an_expired_envelope_with_an_operation_run_still_fails(monkeypatch) -> None:
    _production(monkeypatch, now=_EXPIRED)
    headers = _drain_headers(extra={"operation_run_id": str(uuid.uuid4())})

    result, failures, projected = _apply(monkeypatch, headers)

    assert result.state == "FAILURE"
    assert failures == ["late"]
    assert projected == ["late"]


def test_an_expired_envelope_of_a_non_periodic_task_still_fails(monkeypatch) -> None:
    task = SimpleNamespace(
        name="app.workers.tasks.generate_content_image",
        request=SimpleNamespace(id="late", retries=0, headers=None),
    )
    target = str(uuid.uuid4())
    _production(monkeypatch, now=_EXPIRED)
    task.request.headers = dispatch_auth.stamp_dispatch_headers(
        task_name=task.name, task_id="late", args=[target], kwargs={}, retries=0,
        headers={}, now=ISSUED,
    )

    with pytest.raises(dispatch_auth.DispatchAuthorizationError, match="expired"):
        dispatch_auth.AuthenticatedTask.before_start(task, "late", (target,), {})


def test_a_forged_periodic_drain_that_is_not_expired_still_fails(monkeypatch) -> None:
    _production(monkeypatch, now=ISSUED + 1)
    headers = {**_drain_headers(), dispatch_envelope.SIGNATURE_HEADER: "0" * 64}

    result, failures, projected = _apply(monkeypatch, headers)

    assert result.state == "FAILURE"
    assert failures == ["late"]


def test_the_expired_error_is_still_a_dispatch_authorization_error() -> None:
    assert issubclass(dispatch_auth.ExpiredDispatchEnvelope, dispatch_auth.DispatchAuthorizationError)


# ── D. 주기가 봉투 수명보다 긴 작업·위조 봉투는 만료돼도 실패 ─────────────────────


def _beat_entry(task_name: str) -> dict:
    return next(
        entry for entry in celery_app.conf.beat_schedule.values() if entry["task"] == task_name
    )


def _beat_headers(entry: dict) -> dict[str, str]:
    return dispatch_auth.stamp_dispatch_headers(
        task_name=entry["task"],
        task_id="late",
        args=list(entry.get("args") or []),
        kwargs={},
        retries=0,
        headers=dict((entry.get("options") or {}).get("headers") or {}),
        now=ISSUED,
    )


@pytest.mark.parametrize(
    "task_name",
    [
        "app.workers.tasks.run_weekly_monitoring",  # 월 02:00
        "app.workers.tasks.nightly_content_generation",  # 매일 23:00
    ],
)
def test_an_expired_envelope_of_a_long_period_beat_task_still_fails(monkeypatch, task_name) -> None:
    entry = _beat_entry(task_name)
    args = tuple(entry.get("args") or ())
    _production(monkeypatch, now=_EXPIRED)
    headers = _beat_headers(entry)
    task = SimpleNamespace(
        name=task_name, request=SimpleNamespace(id="late", retries=0, headers=headers)
    )

    assert dispatch_auth._is_run_less_periodic_dispatch(task_name, headers) is False
    with pytest.raises(dispatch_auth.DispatchAuthorizationError, match="expired"):
        dispatch_auth.AuthenticatedTask.before_start(task, "late", args, {})


def test_an_expired_every_minute_drain_is_ignored_not_failed(monkeypatch) -> None:
    _production(monkeypatch, now=_EXPIRED)
    task = SimpleNamespace(
        name=DRAIN_TASK,
        request=SimpleNamespace(id="late", retries=0, headers=_drain_headers()),
    )
    with pytest.raises(Ignore):
        dispatch_auth.AuthenticatedTask.before_start(task, "late", (), {})


def test_a_forged_and_expired_periodic_envelope_fails_instead_of_being_ignored(
    monkeypatch,
) -> None:
    _production(monkeypatch, now=_EXPIRED)
    headers = {**_drain_headers(), dispatch_envelope.SIGNATURE_HEADER: "0" * 64}

    result, failures, _projected = _apply(monkeypatch, headers)

    assert result.state == "FAILURE"
    assert isinstance(result.result, dispatch_auth.DispatchAuthorizationError)
    assert not isinstance(result.result, dispatch_auth.ExpiredDispatchEnvelope)
    assert failures == ["late"]


@pytest.mark.parametrize(
    ("schedule", "period"),
    [
        (crontab(minute="*"), 60.0),
        (crontab(minute="*/5"), 300.0),
        (crontab(minute="0,10"), 50 * 60.0),  # 정시를 넘는 간격이 가장 길다
        (crontab(minute=0), 3600.0),
        (crontab(minute=0, hour="*/2"), None),
        (crontab(hour=23, minute=0), None),
        (crontab(hour=2, minute=0, day_of_week=1), None),
        (timedelta(seconds=30), 30.0),
        (celery_schedule(timedelta(minutes=10)), 600.0),
        (45, 45.0),
    ],
)
def test_schedule_period_seconds(schedule, period) -> None:
    assert dispatch_auth._schedule_period_seconds(schedule) == period


def test_only_beat_entries_within_the_envelope_lifetime_are_droppable() -> None:
    droppable = set()
    for entry in celery_app.conf.beat_schedule.values():
        if dispatch_auth._is_run_less_periodic_dispatch(entry["task"], _beat_headers(entry)):
            droppable.add(entry["task"])
            period = dispatch_auth._schedule_period_seconds(entry["schedule"])
            assert period is not None
            assert period <= dispatch_envelope.DISPATCH_TTL_SECONDS
    assert DRAIN_TASK in droppable
    for long_period in (
        "app.workers.tasks.run_weekly_monitoring",
        "app.workers.tasks.nightly_content_generation",
        "app.workers.tasks.run_monthly_reports",
        "app.workers.tasks.run_monthly_sov_measurement",
        "app.workers.naver_sync.weekly_naver_source_sync",
        "app.workers.tasks.weekly_generation_rejection_rollup",
        "app.workers.tasks.purge_expired_leads",
    ):
        assert long_period not in droppable


# ── E. 실제 Heart가 신호로 liveness 파일을 갱신한다 ──────────────────────────────


def test_a_real_heart_send_touches_the_liveness_file(monkeypatch, tmp_path) -> None:
    from celery.worker.heartbeat import Heart

    path = tmp_path / "hb"
    monkeypatch.setattr(worker_liveness, "HEARTBEAT_FILE", path)
    sent: list[str] = []
    # EventDispatcher(enabled=True)와 같은 표면: Heart가 쓰는 속성만 둔다(브로커 없이).
    eventer = SimpleNamespace(
        enabled=True,
        on_enabled=set(),
        on_disabled=set(),
        send=lambda event, **_fields: sent.append(event),
    )
    timer = SimpleNamespace(call_repeatedly=lambda *_a, **_k: object(), cancel=lambda _t: None)

    heart = Heart(timer, eventer, interval=2.0)
    heart.start()  # worker-online도 _send를 거친다

    assert sent == ["worker-online"]
    assert path.exists()
    os.utime(path, (0, 0))
    heart._send("worker-heartbeat")
    assert sent[-1] == "worker-heartbeat"
    assert worker_liveness.heartbeat_age_seconds(path=path) < 60

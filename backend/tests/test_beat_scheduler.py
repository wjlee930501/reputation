"""Beat liveness(감사 F8)와 새 Beat로의 RedBeat 잠금 인계(2026-11-13).

인계 테스트는 실제 Redis에서 두 스케줄러를 돌린다 — 락은 redis-py Lock이고 알림은 Redis 키라서
가짜로는 "두 Beat가 동시에 보내지 않는다"를 보일 수 없다.
"""

from __future__ import annotations

import os
import threading
import uuid
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
import redis
from celery import Celery
from celery.signals import beat_init
from redbeat.schedulers import RedBeatScheduler, acquire_distributed_beat_lock

from app.core.celery_app import celery_app
from app.workers import beat_scheduler, health_server, worker_liveness
from tests.db_env import require_redis_url


# ── 생존 증거 ─────────────────────────────────────────────────────────────────
def test_tick_touches_the_beat_liveness_file_and_keeps_the_interval(monkeypatch, tmp_path: Path):
    target = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", target)
    monkeypatch.setattr(RedBeatScheduler, "tick", lambda self, *args, **kwargs: 7.5)
    scheduler = object.__new__(beat_scheduler.LivenessRedBeatScheduler)
    scheduler.lock_key = None  # 락 없는 설정 — RedBeat tick 그대로

    assert scheduler.tick() == 7.5
    assert target.exists()


def test_failed_tick_does_not_touch_the_file(monkeypatch, tmp_path: Path):
    target = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", target)

    def lost_lock(self, *args, **kwargs):
        raise RuntimeError("lock not owned")

    monkeypatch.setattr(RedBeatScheduler, "tick", lost_lock)
    scheduler = object.__new__(beat_scheduler.LivenessRedBeatScheduler)
    scheduler.lock_key = None

    with pytest.raises(RuntimeError):
        scheduler.tick()
    # 락을 잃은 tick은 증거를 남기지 않는다 — 예외가 beat를 끝내고 Cloud Run이 다시 띄운다.
    assert not target.exists()


def test_celery_beat_uses_the_liveness_scheduler():
    assert celery_app.conf.beat_scheduler == "app.workers.beat_scheduler.LivenessRedBeatScheduler"


def test_beat_entrypoint_starts_the_health_server_with_tick_checks():
    entrypoint = (Path(__file__).resolve().parents[1] / "docker-entrypoint.sh").read_text()
    beat_branch = entrypoint.split("  beat)", 1)[1].split(";;", 1)[0]
    assert "python -m app.workers.health_server --beat-tick &" in beat_branch


def test_redbeat_blocking_lock_handler_is_replaced():
    live = [
        ref() if isinstance(ref, weakref.ReferenceType) else ref
        for _key, ref in beat_init.receivers
    ]
    assert acquire_distributed_beat_lock not in live
    assert beat_scheduler._on_beat_init in live


# ── 실제 Redis에서 두 Beat ───────────────────────────────────────────────────
class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def beat_env(monkeypatch, tmp_path: Path):
    url = require_redis_url("INTEGRATION_REDIS_URL")
    prefix = f"test-beat-{uuid.uuid4().hex}"
    client = redis.Redis.from_url(url)
    tick_file = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", tick_file)
    clock = _Clock()
    sent: list[tuple[str, str]] = []
    apps: list[Celery] = []

    def make(name: str) -> beat_scheduler.LivenessRedBeatScheduler:
        # 인스턴스마다 다른 앱(=다른 Redis 연결) — 다른 프로세스의 Beat처럼.
        app = Celery(f"beat-test-{name}", set_as_current=False)
        app.conf.update(
            redbeat_redis_url=url,
            redbeat_key_prefix=prefix,
            redbeat_lock_timeout=300,
            beat_max_loop_interval=30,
            beat_schedule={"every-second": {"task": "test.noop", "schedule": 1.0}},
        )
        apps.append(app)
        scheduler = beat_scheduler.LivenessRedBeatScheduler(app=app)
        scheduler._now = clock  # type: ignore[method-assign]
        # 브로커에 연결하지 않고 "보냈다"만 센다.
        scheduler.__dict__["_maybe_due_kwargs"] = {}

        def record(entry, **_kwargs):
            sent.append((name, entry.name))
            return 1.0

        scheduler.maybe_due = record  # type: ignore[method-assign]
        return scheduler

    env = SimpleNamespace(
        make=make, client=client, prefix=prefix, clock=clock, sent=sent, tick_file=tick_file
    )
    try:
        client.ping()
    except redis.RedisError as exc:
        pytest.fail(
            f"INTEGRATION_REDIS_URL is set but Redis is unreachable ({type(exc).__name__})."
        )
    yield env
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)
    for app in apps:
        conn = getattr(app, "redbeat_redis", None)
        if conn is not None:
            conn.close()
    client.close()


def _senders(env) -> set[str]:
    names = {name for name, _entry in env.sent}
    env.sent.clear()
    return names


def test_holder_releases_to_the_newly_announced_beat(beat_env):
    old = beat_env.make("old")
    old.announce()
    old.tick()
    assert _senders(beat_env) == {"old"}

    new = beat_env.make("new")
    new.announce()  # beat_init
    # 옛 보유자가 아직 락을 쥐고 있다 — 새 Beat는 보내지 않지만 생존 증거는 남긴다.
    beat_env.tick_file.unlink(missing_ok=True)
    assert new.tick() == new.lock_wait_interval
    assert beat_env.tick_file.exists()
    assert _senders(beat_env) == set()

    # 옛 보유자는 다음 tick에 알림을 보고 놓는다 — 그 tick에 보내지 않는다.
    old.tick()
    assert _senders(beat_env) == set()
    assert not old.lock.owned()

    new.tick()
    assert _senders(beat_env) == {"new"}

    # 물러난 Beat는 락이 잡혀 있는 동안 다시 시도하지 않고, 보내지 않는다.
    for _ in range(3):
        beat_env.clock.now += 120
        old.tick()
        new.tick()
        assert _senders(beat_env) == {"new"}


def test_only_the_lock_holder_ever_dispatches(beat_env):
    a = beat_env.make("a")
    b = beat_env.make("b")
    a.announce()
    b.announce()
    for _ in range(6):
        a.tick()
        b.tick()
        senders = _senders(beat_env)
        assert len(senders) <= 1
        holders = {
            s for s, sch in (("a", a), ("b", b)) if sch.lock is not None and sch.lock.owned()
        }
        assert len(holders) <= 1
        assert senders <= holders


def test_superseded_beat_takes_over_when_the_announcer_never_takes_the_lock(beat_env):
    old = beat_env.make("old")
    old.announce()
    old.tick()
    _senders(beat_env)

    ghost = beat_env.make("ghost")
    ghost.announce()  # 뜨자마자 죽은 새 Beat — 다시 tick하지 않는다

    old.tick()  # 물러난다
    assert not old.lock.owned()
    beat_env.clock.now += old.superseded_takeover_seconds - 1
    old.tick()
    assert _senders(beat_env) == set()

    # 락이 계속 비어 있으면 이어 받는다 — 스케줄이 끊기지 않는다.
    beat_env.clock.now += 2
    old.tick()
    assert _senders(beat_env) == {"old"}
    # 이어 받을 때 본 알림이 기준이라 같은 알림으로 다시 물러나지 않는다.
    old.tick()
    assert _senders(beat_env) == {"old"}
    assert old.lock.owned()


def test_missing_or_unreadable_announcement_does_not_step_down(beat_env, monkeypatch):
    holder = beat_env.make("holder")
    holder.announce()
    holder.tick()
    _senders(beat_env)

    beat_env.client.delete(beat_scheduler.announcement_key(beat_env.prefix))
    holder.tick()
    assert _senders(beat_env) == {"holder"}

    def broken(self):
        raise redis.ConnectionError("announcement read failed")

    monkeypatch.setattr(beat_scheduler.LivenessRedBeatScheduler, "_read_announcement", broken)
    holder.tick()
    assert _senders(beat_env) == {"holder"}
    assert holder.lock.owned()


def test_schedule_definitions_are_written_only_after_taking_the_lock(beat_env):
    holder = beat_env.make("holder")
    holder.announce()
    holder.tick()
    schedule_key = holder.app.redbeat_conf.schedule_key
    entries_after_holder = beat_env.client.zcard(schedule_key)
    assert entries_after_holder >= 1

    beat_env.client.delete(schedule_key)
    waiter = beat_env.make("waiter")  # 생성자는 정의를 쓰지 않는다
    waiter.announce()
    waiter.tick()  # 아직 보유자가 있다
    assert beat_env.client.zcard(schedule_key) == 0

    holder.tick()  # 놓는다
    waiter.tick()  # 잡고 정의를 쓴다
    assert beat_env.client.zcard(schedule_key) == entries_after_holder


def test_beat_init_announces_without_blocking_on_a_held_lock(beat_env):
    holder = beat_env.make("holder")
    holder.announce()
    holder.tick()

    newcomer = beat_env.make("newcomer")

    class _Service:  # celery Service 대역 — 신호 sender는 해시할 수 있어야 한다
        scheduler = newcomer

    sender = _Service()
    done = threading.Event()

    def fire():
        beat_init.send(sender=sender)
        done.set()

    thread = threading.Thread(target=fire, daemon=True)
    thread.start()
    # redbeat의 원래 수신자였다면 보유자가 놓을 때까지 막혔다.
    assert done.wait(5), "beat_init blocked on the RedBeat lock"
    assert newcomer._announcement is not None
    assert newcomer.lock is None or not newcomer.lock.owned()
    stored = beat_env.client.get(beat_scheduler.announcement_key(beat_env.prefix))
    assert stored.decode() == newcomer._announcement


def test_waiting_beat_passes_live_and_a_stalled_wait_fails_it(beat_env, monkeypatch):
    holder = beat_env.make("holder")
    holder.announce()
    holder.tick()
    waiter = beat_env.make("waiter")
    waiter._superseded = True  # 물러난 Beat — 보유자가 있는 동안 기다리기만 한다

    monkeypatch.setattr(health_server, "_CHECK_BEAT_TICK", True)
    monkeypatch.setattr(health_server, "_STARTED_AT", 0.0)
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STALE_SECONDS", 600)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STARTUP_GRACE_SECONDS", 900)

    waiter.tick()
    stamp = os.stat(beat_env.tick_file).st_mtime
    # 기동 유예가 한참 지났어도 대기 루프가 도는 한 살아 있다.
    monkeypatch.setattr(health_server.time, "time", lambda: stamp + 30)
    assert health_server.is_live() is True
    assert _senders(beat_env) == {"holder"}

    # 대기 루프가 멈추면(파일이 안 바뀌면) 종전처럼 /live가 실패한다.
    monkeypatch.setattr(health_server.time, "time", lambda: stamp + 601)
    assert health_server.is_live() is False


def test_newcomer_logs_once_when_it_waits_longer_than_the_lock_ttl(beat_env, caplog):
    holder = beat_env.make("holder")
    holder.announce()
    holder.tick()
    newcomer = beat_env.make("newcomer")
    newcomer.announce()
    # 보유자가 인계 코드 없는 옛 판이라 물러나지 않는 경우(머지 뒤 첫 배포).
    holder._superseded_by = lambda: None  # type: ignore[method-assign]

    with caplog.at_level("WARNING", logger="app.workers.beat_scheduler"):
        newcomer.tick()
        beat_env.clock.now += newcomer.lock_timeout - 1
        newcomer.tick()
        assert "beat_lock_wait_long" not in caplog.text
        beat_env.clock.now += 2
        newcomer.tick()
        newcomer.tick()
    assert caplog.text.count("beat_lock_wait_long") == 1
    assert _senders(beat_env) == {"holder"}


def test_redis_error_while_waiting_keeps_the_loop_alive(beat_env, monkeypatch):
    waiter = beat_env.make("waiter")
    waiter._announcement = "already-announced"

    def broken(self):
        raise redis.ConnectionError("redis down")

    monkeypatch.setattr(beat_scheduler.LivenessRedBeatScheduler, "_try_acquire", broken)
    beat_env.tick_file.unlink(missing_ok=True)
    assert waiter.tick() == waiter.lock_wait_interval
    # 루프는 돌고 있다 — 증거를 남겨 재시작(=다시 알려 보유자를 밀어내기)을 부르지 않는다.
    assert beat_env.tick_file.exists()
    assert _senders(beat_env) == set()

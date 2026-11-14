"""RedBeat 스케줄러 + 스케줄 루프 생존 증거(2026-10 자율 운영 감사 F8) + 새 Beat로의 잠금 인계(2026-11-13).

생존 증거: Beat의 Cloud Run `/live`는 tick이 끝날 때마다 갱신되는 인스턴스 로컬 파일의 나이를 본다
(`--beat-tick`). RedBeat는 매 루프(최대 30초, `beat_max_loop_interval`)마다 `tick`에서 분산 락을 연장하고
due 엔트리를 보낸다. 루프가 멈추면 파일이 오래돼 Cloud Run이 Beat를 재시작한다. liveness가 Redis를 직접
읽지 않으므로 Redis 장애 동안 재시작을 반복하지 않는다.

잠금 인계: Cloud Run은 퇴역한 리비전의 인스턴스를 언제 내릴지 보장하지 않는다. 2026-11-06까지는 옛 Beat가
배포 몇 초 뒤 SIGTERM을 받아 락을 놓았지만, 11-08부터는 설정 변경 없이 매 배포마다 약 2시간 동안 종료 신호 없이
살아남아 락을 계속 연장했다(min-instances=1, CPU 항상 할당, 요청 없음). RedBeat 원래 동작에서 새 Beat는 `beat_init`의 막히는 `lock.acquire()`
안에서 기다리느라 tick을 한 번도 못 해, 기동 유예(900초)가 끝날 때마다 `/live` 503으로 재시작됐다(약 15분마다
ERROR). 그동안 옛 코드가 계속 스케줄을 보냈다.

그래서 여기서는 —
- 새로 뜬 Beat가 `beat_init`에서 자기 등장을 Redis에 알린다(announcement). 락은 잡지 않는다.
- 락 보유자는 tick마다 알림을 읽고, 락을 잡을 때 본 값과 달라졌으면 **락을 놓고 대기로 물러난다**(종료하지 않는다 —
  같은 인스턴스에서 컨테이너가 다시 뜨면 다시 알려 새 Beat를 밀어낸다).
- 락이 없는 Beat는 tick 안에서 막히지 않는 시도로 락을 기다리며(5초 간격) 생존 파일을 갱신한다. 그래서 기다리는
  Beat가 `/live`로 재시작되지 않는다. 대기 루프 자체가 멈추면 파일이 오래돼 종전처럼 재시작된다.
- 한 번 물러난 Beat는 락이 `SUPERSEDED_TAKEOVER_SECONDS` 동안 계속 비어 있을 때만 다시 잡는다(알린 Beat가 먼저 잡게
  양보한다). 알린 Beat가 뜨다 죽었으면 그 뒤 옛 Beat가 이어 받아 스케줄이 끊기지 않는다.
- 스케줄 정의(`setup_schedule`)는 락을 잡은 Beat만 Redis에 쓴다 — 보내는 코드와 정의가 같은 판이다.

락은 여전히 유일한 발송 권한이다. 알림은 보유자가 놓게 만들 뿐, 락 없는 Beat가 보내게 하지 않는다. 알림을 읽지
못하거나 키가 없으면 물러나지 않는다(종전 동작). 락을 잃으면(`lock.extend` 실패) 종전처럼 예외로 beat가 끝난다.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from typing import Any

import redis
from celery.signals import beat_init
from redbeat.schedulers import (
    LUA_EXTEND_TO_SCRIPT,
    RedBeatScheduler,
    acquire_distributed_beat_lock,
    get_redis,
)

from app.workers import worker_liveness

logger = logging.getLogger(__name__)

# 락을 기다리는 Beat가 시도하는 간격(초). 보유자가 놓으면 알린 Beat가 몇 초 안에 잡는다.
LOCK_WAIT_INTERVAL_SECONDS = 5.0
# 물러난 Beat가 다시 락을 잡기 전에 락이 계속 비어 있어야 하는 시간(초). 알린 Beat는 5초마다 시도하므로
# 이 시간 동안 비어 있으면 알린 Beat가 없다(뜨다 죽었다)고 보고 이어 받는다.
SUPERSEDED_TAKEOVER_SECONDS = 60.0


def announcement_key(key_prefix: str) -> str:
    return f"{key_prefix}:reputation:beat-announcement"


def _instance_label() -> str:
    # Cloud Run은 K_REVISION을 넣는다. 로그로 누가 알렸는지 보이게 리비전을 앞에 둔다.
    return os.environ.get("K_REVISION") or socket.gethostname() or "beat"


class LivenessRedBeatScheduler(RedBeatScheduler):
    _leading = False
    _superseded = False
    _baseline: str | None = None
    _announcement: str | None = None
    _lock_free_since: float | None = None
    _schedule_installed = False
    _wait_started: float | None = None
    _long_wait_logged = False

    # 테스트가 바꾼다.
    lock_wait_interval = LOCK_WAIT_INTERVAL_SECONDS
    superseded_takeover_seconds = SUPERSEDED_TAKEOVER_SECONDS

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    # ── 스케줄 정의는 락 보유자만 쓴다 ─────────────────────────────────────────
    def setup_schedule(self) -> None:
        # Scheduler.__init__이 부른다. 락을 잡기 전에는 Redis 정의를 건드리지 않는다
        # (퇴역 인스턴스가 다시 떠도 지금 보유자의 정의를 옛 판으로 덮지 않는다).
        return None

    def _install_schedule(self) -> None:
        if not self._schedule_installed:
            RedBeatScheduler.setup_schedule(self)
            self._schedule_installed = True

    # ── 알림과 락 ─────────────────────────────────────────────────────────────
    @property
    def _announcement_key(self) -> str:
        return announcement_key(self.app.redbeat_conf.key_prefix)

    def _client(self) -> Any:
        return get_redis(self.app)

    def _ensure_lock(self) -> None:
        if self.lock is not None:
            return
        client = self._client()
        lock = client.lock(
            self.lock_key,
            timeout=self.lock_timeout,
            sleep=self.max_interval,
        )
        # redbeat와 같게 — redis-py의 extend는 TTL을 더하므로 "이 TTL로 맞추기" 스크립트로 바꾼다.
        lock.lua_extend = client.register_script(LUA_EXTEND_TO_SCRIPT)
        self.lock = lock

    def announce(self) -> None:
        """새 Beat의 등장을 알린다. 락 보유자는 다음 tick에 이 값을 보고 물러난다."""
        token = f"{_instance_label()}/{uuid.uuid4().hex}"
        self._client().set(self._announcement_key, token)
        self._announcement = token
        logger.info("beat_announced announcement=%s", token)

    def _read_announcement(self) -> str | None:
        value = self._client().get(self._announcement_key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    def _superseded_by(self) -> str | None:
        """보유자가 락을 잡은 뒤 다른 Beat가 알렸으면 그 알림. 읽지 못하거나 키가 없으면 None(물러나지 않는다)."""
        try:
            current = self._read_announcement()
        except redis.RedisError:
            logger.warning("beat_announcement_read_failed", exc_info=True)
            return None
        if current is None or current == self._baseline:
            return None
        return current

    def _step_down(self, announcement: str) -> None:
        logger.warning(
            "beat_superseded releasing_lock=true announcement=%s my_announcement=%s",
            announcement,
            self._announcement,
        )
        try:
            if self.lock is not None and self.lock.owned():
                self.lock.release()
        except redis.RedisError:
            # 놓지 못한 락은 TTL(300초) 뒤 풀린다. 여기서는 더 보내지 않는 것만 지킨다.
            logger.warning("beat_lock_release_failed", exc_info=True)
        self._leading = False
        self._superseded = True
        self._lock_free_since = None
        self._wait_started = None

    def _try_acquire(self) -> bool:
        """막히지 않고 락을 한 번 시도한다. 잡으면 그때 본 알림을 기준으로 남긴다."""
        self._ensure_lock()
        if self._announcement is None and not self._superseded:
            # beat_init에서 알리지 못했으면(Redis 장애) 다시 알린다.
            self.announce()
        if self._superseded:
            if self._client().exists(self.lock_key):
                self._lock_free_since = None
                return False
            now = self._now()
            if self._lock_free_since is None:
                self._lock_free_since = now
            if now - self._lock_free_since < self.superseded_takeover_seconds:
                return False
        # 기준 알림은 시도 전에 읽는다 — 시도와 읽기 사이에 새 알림이 오면 첫 tick에 물러난다.
        baseline = self._read_announcement()
        if not self.lock.acquire(blocking=False):
            return False
        self._baseline = baseline
        self._leading = True
        self._lock_free_since = None
        self._wait_started = None
        self._long_wait_logged = False
        logger.info(
            "beat_lock_acquired announcement=%s superseded_before=%s",
            baseline,
            self._superseded,
        )
        return True

    def _note_waiting(self) -> None:
        # 막히는 대기의 /live 재시작(ERROR)이 하던 신호를 대신한다 — 알린 Beat가 락 TTL보다 오래 못 잡으면 한 번 남긴다.
        now = self._now()
        if self._wait_started is None:
            self._wait_started = now
            return
        if self._superseded or self._long_wait_logged:
            return
        waited = now - self._wait_started
        if waited > self.lock_timeout:
            self._long_wait_logged = True
            holder = None
            try:
                holder = self._read_announcement()
            except redis.RedisError:
                pass
            logger.warning(
                "beat_lock_wait_long waited_seconds=%d my_announcement=%s current_announcement=%s",
                waited,
                self._announcement,
                holder,
            )

    # ── 루프 ──────────────────────────────────────────────────────────────────
    def tick(self, *args: Any, **kwargs: Any) -> float:
        if not self.lock_key:
            interval = super().tick(*args, **kwargs)
            worker_liveness.touch_heartbeat(path=worker_liveness.BEAT_TICK_FILE)
            return interval

        if self._leading:
            announcement = self._superseded_by()
            if announcement is not None:
                self._step_down(announcement)

        if not self._leading:
            try:
                acquired = self._try_acquire()
            except redis.RedisError:
                # 대기 루프는 살아 있다 — 증거를 남긴다. Redis 장애로 재시작을 반복하지 않고, 재시작된 컨테이너가
                # 다시 알려 지금 보유자를 밀어내지도 않는다. 호출이 멈추면(소켓 timeout 밖) 파일이 오래돼 재시작된다.
                logger.warning("beat_lock_wait_redis_error", exc_info=True)
                worker_liveness.touch_heartbeat(path=worker_liveness.BEAT_TICK_FILE)
                return self.lock_wait_interval
            if not acquired:
                self._note_waiting()
                worker_liveness.touch_heartbeat(path=worker_liveness.BEAT_TICK_FILE)
                return self.lock_wait_interval
            self._install_schedule()

        interval = super().tick(*args, **kwargs)
        worker_liveness.touch_heartbeat(path=worker_liveness.BEAT_TICK_FILE)
        return interval

    def close(self) -> None:
        # redbeat close는 소유한 락만 놓는다. 대기 중이던 락 객체도 그대로 넘긴다.
        self._leading = False
        super().close()


def _on_beat_init(sender: Any = None, **kwargs: Any) -> None:
    scheduler = sender.scheduler
    if isinstance(scheduler, LivenessRedBeatScheduler):
        if not scheduler.lock_key:
            return
        # 막히는 락 대기 대신 알림만 한다. 락은 tick 안에서 막히지 않게 기다린다.
        scheduler.announce()
        return
    acquire_distributed_beat_lock(sender=sender, **kwargs)


# redbeat는 import 때 막히는 락 획득 수신자를 beat_init에 건다. 이 스케줄러에서는 그 대기를 tick 안으로 옮긴다
# (다른 스케줄러면 _on_beat_init이 원래 수신자를 그대로 부른다).
beat_init.disconnect(acquire_distributed_beat_lock)
beat_init.connect(_on_beat_init, weak=False)

"""RedBeat 스케줄러 + 스케줄 루프 생존 증거(2026-10 자율 운영 감사 F8).

Beat의 Cloud Run `/live`는 부모 프로세스가 살아 있는지만 봤다. 스케줄 루프가 Redis 등에 막혀
멈춰도 프로세스는 살아 있어 사람이 재기동해야 했다. RedBeat는 매 루프(최대 30초,
`beat_max_loop_interval`)마다 `tick`에서 분산 락을 연장하고 due 엔트리를 보낸다. tick이 끝날 때마다
인스턴스 로컬 파일을 갱신하면 health server가 그 나이로 멈춘 루프를 알아챈다.

락을 잃으면 `tick`의 `lock.extend`가 예외를 던져 beat 프로세스가 끝난다 — 그 tick은 파일을 갱신하지 않는다.
liveness가 Redis를 직접 읽지 않으므로 Redis 장애 동안 재시작을 반복하지 않는다.
"""

from __future__ import annotations

from typing import Any

from redbeat.schedulers import RedBeatScheduler

from app.workers import worker_liveness


class LivenessRedBeatScheduler(RedBeatScheduler):
    def tick(self, *args: Any, **kwargs: Any) -> float:
        interval = super().tick(*args, **kwargs)
        worker_liveness.touch_heartbeat(path=worker_liveness.BEAT_TICK_FILE)
        return interval

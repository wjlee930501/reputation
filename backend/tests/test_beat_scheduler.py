"""Beat liveness — RedBeat tick이 인스턴스 로컬 파일을 갱신한다(감사 F8)."""

from pathlib import Path

from redbeat.schedulers import RedBeatScheduler

from app.core.celery_app import celery_app
from app.workers import beat_scheduler, worker_liveness


def test_tick_touches_the_beat_liveness_file_and_keeps_the_interval(monkeypatch, tmp_path: Path):
    target = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", target)
    monkeypatch.setattr(RedBeatScheduler, "tick", lambda self, *args, **kwargs: 7.5)
    scheduler = object.__new__(beat_scheduler.LivenessRedBeatScheduler)

    assert scheduler.tick() == 7.5
    assert target.exists()


def test_failed_tick_does_not_touch_the_file(monkeypatch, tmp_path: Path):
    target = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", target)

    def lost_lock(self, *args, **kwargs):
        raise RuntimeError("lock not owned")

    monkeypatch.setattr(RedBeatScheduler, "tick", lost_lock)
    scheduler = object.__new__(beat_scheduler.LivenessRedBeatScheduler)

    try:
        scheduler.tick()
    except RuntimeError:
        pass
    # 락을 잃은 tick은 증거를 남기지 않는다 — 예외가 beat를 끝내고 Cloud Run이 다시 띄운다.
    assert not target.exists()


def test_celery_beat_uses_the_liveness_scheduler():
    assert celery_app.conf.beat_scheduler == "app.workers.beat_scheduler.LivenessRedBeatScheduler"


def test_beat_entrypoint_starts_the_health_server_with_tick_checks():
    entrypoint = (Path(__file__).resolve().parents[1] / "docker-entrypoint.sh").read_text()
    beat_branch = entrypoint.split("  beat)", 1)[1].split(";;", 1)[0]
    assert "python -m app.workers.health_server --beat-tick &" in beat_branch

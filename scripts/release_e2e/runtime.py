"""Test-only transport/storage/calendar adapters; business gates remain unmodified."""

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path("/evidence")
assert os.environ.get("APP_ENV") == "test"
assert os.environ.get("DATABASE_URL", "").endswith("@qa-db:5432/reputation_release_e2e")
assert not (Path.home() / ".config/gcloud").exists()

import arrow  # noqa: E402

_REAL_NOW = arrow.now


def calendar_now(tz=None):
    clock = ROOT / "clock.txt"
    return (
        arrow.get(clock.read_text().strip()).to(tz or "local")
        if clock.exists()
        else _REAL_NOW(tz)
    )


arrow.now = calendar_now


class Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.path = (
            ROOT / "assets" / hashlib.sha256(f"{bucket}/{name}".encode()).hexdigest()
        )
        self.content_type = "image/png"

    def upload_from_file(self, file, **kwargs):
        self.path.write_bytes(file.read())

    def download_as_bytes(self, **kwargs):
        return self.path.read_bytes()

    def exists(self, **kwargs):
        return self.path.exists()

    def reload(self, **kwargs):
        self.size = self.path.stat().st_size

    def generate_signed_url(self, **kwargs):
        return f"http://qa-capture:8080/assets/{self.path.name}.png"


CAPTURE_BASE_URL = "http://qa-capture:8080"


def _redirect_openrouter_gateway():
    """공급자별 클라이언트 주입 대신 게이트웨이 하나를 합성 서버로 돌린다.

    콘텐츠 생성·독립 검수·Essence·양쪽 SoV·이미지 생성/검수가 전부
    `app.services.openrouter`를 통해 나간다. 따라서 가로챌 지점도 하나다 — base URL을
    qa-capture로 바꾸고 이미 만들어진 클라이언트 캐시를 비운다.

    서비스 모듈보다 **먼저** 불러야 한다. sov_engine은 import 시점에 모듈 수준
    클라이언트를 만들기 때문에, 순서가 뒤집히면 그 두 개만 진짜 openrouter.ai를
    가리킨 채로 남는다.
    """
    from app.core.config import settings
    from app.services import openrouter

    settings.OPENROUTER_API_KEY = "synthetic-openrouter"
    openrouter.OPENROUTER_BASE_URL = CAPTURE_BASE_URL
    openrouter.reset_clients_for_tests()
    return openrouter


def install_boundaries():
    openrouter = _redirect_openrouter_gateway()

    from app.services import (
        gcs_utils,
        indexnow,
        report_engine,
        sov_engine,
    )

    gcs_utils._gcs_client = SimpleNamespace(
        bucket=lambda name: SimpleNamespace(blob=lambda key: Blob(name, key))
    )
    # sov_engine의 두 클라이언트는 import 시점에 만들어져 모듈 전역에 묶인다. 위 reset은
    # openrouter의 캐시만 비우므로, 다른 경로가 sov_engine을 먼저 불러 왔다면 그 두 개만
    # 진짜 openrouter.ai를 가리킨 채 남는다 — 여기서 새 base URL로 다시 만든다.
    sov_engine.openai_client = openrouter.async_client(
        timeout=sov_engine.OPENAI_TIMEOUT_SECONDS
    )
    sov_engine.openai_query_client = openrouter.async_client(
        timeout=sov_engine.OPENAI_TIMEOUT_SECONDS
    )
    report_engine._upload_to_gcs = lambda path, slug, filename: str(path)
    from app.services import doctor_report_artifact

    doctor_report_artifact._upload_to_gcs = report_engine._upload_to_gcs
    indexnow.INDEXNOW_ENDPOINT = "http://qa-capture:8080/indexnow"

    # Month-end business dates must agree with arrow.now. The OS, broker,
    # dispatch signatures and monotonic timers retain their real clocks.
    class CalendarDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = calendar_now(tz).datetime
            return value if tz is not None else value.replace(tzinfo=None)

    from app.workers import tasks

    tasks.datetime = CalendarDateTime


if __name__ == "__main__":
    os.environ["SERVICE"] = (
        "worker"
        if sys.argv[1] == "worker"
        else ("beat" if sys.argv[1] == "beat" else "api")
    )
    install_boundaries()
    from app.core.celery_app import celery_app

    mode = sys.argv[1]
    if mode == "worker":
        celery_app.worker_main(
            [
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
                "--hostname=isolated-e2e@local",
                "-Q",
                "control,default,content,sov,reports,leadgen,certificates",
            ]
        )
    elif mode == "api":
        import uvicorn

        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="warning")
    elif mode == "beat":
        stage = sys.argv[2]
        choices = {
            "baseline": "weekly-sov-monitoring",
            "generate": "nightly-content-generation",
            "publish": "morning-content-auto-publish",
            "measure": "monthly-sov-measurement",
            "report": "monthly-reports",
            "recover": "reconcile-autonomous-workflows",
            "heartbeat": "daily-fleet-heartbeat",
            "index": "drain-indexnow-retries",
        }
        names = {choices[stage], "dispatch-notification-outbox"}
        names.update(
            name for name in celery_app.conf.beat_schedule if name.startswith("canary-")
        )
        schedules = {
            name: {**entry, "schedule": 2.0}
            for name, entry in celery_app.conf.beat_schedule.items()
            if name in names
        }
        (ROOT / f"beat-{stage}-contracts.json").write_text(
            json.dumps(schedules, default=str, indent=2)
        )
        celery_app.conf.update(
            beat_schedule=schedules,
            redbeat_key_prefix=f"qa-{stage}:",
            redbeat_lock_key=f"qa-{stage}:lock",
            beat_max_loop_interval=1,
        )
        celery_app.Beat(
            loglevel="INFO", pidfile="", scheduler="redbeat.RedBeatScheduler"
        ).run()
    elif mode in {"seed", "probe"}:
        import runpy

        runpy.run_path(f"/qa/{mode}.py", run_name="__main__")

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


def install_boundaries():
    from app.services import (
        gcs_utils,
        image_engine,
        indexnow,
        report_engine,
        sov_engine,
    )
    from google import genai

    gcs_utils._gcs_client = SimpleNamespace(
        bucket=lambda name: SimpleNamespace(blob=lambda key: Blob(name, key))
    )
    client = genai.Client(
        api_key="synthetic-google", http_options={"base_url": "http://qa-capture:8080"}
    )
    image_engine._google_client_instance = client
    sov_engine._gemini_client = client
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

"""Dependency-aware HTTP probes for Cloud Run Celery worker and Beat services."""

from __future__ import annotations

import argparse
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.workers import worker_liveness

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# `--worker-heartbeat`(worker 분기만)일 때 /live가 consumer heartbeat의 나이도 본다. Beat에는
# consumer가 없으므로 끈 채로 둔다. 시작 시각은 기동 유예의 기준점이다.
_CHECK_WORKER_HEARTBEAT = False
_STARTED_AT = time.time()


def _parent_process_alive() -> bool:
    try:
        os.kill(os.getppid(), 0)
    except OSError:
        return False
    return True


def _database_ready() -> bool:
    try:
        with SyncSessionLocal() as db:
            return db.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        return False


def _redis_ready() -> bool:
    client = redis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        return bool(client.ping())
    except redis.RedisError:
        return False
    finally:
        client.close()


def _worker_consumer_alive() -> bool:
    if not _CHECK_WORKER_HEARTBEAT:
        return True
    alive = worker_liveness.consumer_alive(
        started_at=_STARTED_AT,
        stale_seconds=settings.WORKER_LIVENESS_STALE_SECONDS,
        startup_grace_seconds=settings.WORKER_LIVENESS_STARTUP_GRACE_SECONDS,
    )
    if not alive:
        # Cloud Run이 이 응답으로 인스턴스를 재시작한다 — 재시작 사유를 로그에 남긴다.
        logger.warning(
            "worker_liveness_failed reason=consumer_heartbeat_stale age_seconds=%s "
            "stale_seconds=%s",
            worker_liveness.heartbeat_age_seconds(),
            settings.WORKER_LIVENESS_STALE_SECONDS,
        )
    return alive


def is_live() -> bool:
    return _parent_process_alive() and _worker_consumer_alive()


def readiness_checks() -> dict[str, bool]:
    return {
        "celery_parent_alive": _parent_process_alive(),
        "database_connected": _database_ready(),
        "redis_connected": _redis_ready(),
        "release_revision_configured": (
            bool(settings.REPUTATION_RELEASE_REVISION.strip())
            or settings.APP_ENV.lower() != "production"
        ),
    }


def is_ready() -> bool:
    return all(readiness_checks().values())


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server interface
        if self.path == "/live":
            healthy = is_live()
        elif self.path == "/ready":
            healthy = is_ready()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok" if healthy else b"not-ready")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main(argv: list[str] | None = None) -> None:
    global _CHECK_WORKER_HEARTBEAT
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-heartbeat", action="store_true")
    _CHECK_WORKER_HEARTBEAT = parser.parse_args(argv).worker_heartbeat
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("worker health server listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()

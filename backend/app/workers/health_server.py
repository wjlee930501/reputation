"""Dependency-aware HTTP probes for Cloud Run Celery worker and Beat services."""

from __future__ import annotations

import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SyncSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
            healthy = _parent_process_alive()
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


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("worker health server listening on :%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()

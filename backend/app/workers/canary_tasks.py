"""Side-effect-free worker canaries and current-release evidence."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

import redis
from celery.app.task import Task
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.services.notification_contracts import IncidentSlackProjection, validate_message
from app.services.notification_messages import build_open_incident_notification
from app.workers.dispatch_auth import require_dispatch

EXPECTED_QUEUES: Final = ("default", "content", "sov", "reports", "leadgen")
CANARY_MAX_AGE: Final = timedelta(minutes=15)
CANARY_TTL_SECONDS: Final = 20 * 60
_SAFE_REVISION: Final = re.compile(r"^[A-Za-z0-9._-]{7,128}$")


class CanaryPayload(TypedDict):
    queue: str
    release_revision: str
    cloud_run_revision: str | None
    task_id: str
    result: str
    checked_at: str
    checks: dict[str, bool]


@dataclass(frozen=True, slots=True)
class QueueCanaryFacts:
    current: bool
    missing_or_stale_queues: tuple[str, ...]
    release_revision: str | None
    queue_results: dict[str, CanaryPayload]


@dataclass(frozen=True, slots=True)
class CanaryConfigurationError(RuntimeError):
    code: str

    def __str__(self) -> str:
        return self.code


def release_revision() -> str:
    raw = settings.REPUTATION_RELEASE_REVISION.strip()
    if not raw and settings.APP_ENV != "production":
        return "local-dev"
    if not _SAFE_REVISION.fullmatch(raw):
        raise CanaryConfigurationError("RELEASE_REVISION_REQUIRED")
    return raw


def canary_key(revision: str, queue: str) -> str:
    if queue not in EXPECTED_QUEUES:
        raise CanaryConfigurationError("CANARY_QUEUE_UNKNOWN")
    if not _SAFE_REVISION.fullmatch(revision):
        raise CanaryConfigurationError("RELEASE_REVISION_INVALID")
    return f"reputation:worker-canary:v1:{revision}:{queue}"


def _outbox_contract_dry_run() -> None:
    projection = IncidentSlackProjection(
        incident_id=uuid.UUID(int=0),
        hospital_name="시스템 점검",
        severity="LOW",
        customer_impact="고객 데이터나 발행 상태를 변경하지 않는 연결 점검입니다.",
        next_action="조치가 필요하지 않습니다.",
        admin_path="/operations",
        owner_label="자동 점검",
        sla_label="확인 완료",
    )
    intent = build_open_incident_notification(projection, settings.ADMIN_BASE_URL)
    validate_message(intent.message, allowed_admin_base_url=settings.ADMIN_BASE_URL)
    if not intent.dedupe_key or intent.max_attempts < 1:
        raise CanaryConfigurationError("OUTBOX_DRY_RUN_INVALID")


def _observed_queue(task: Task) -> str | None:
    delivery = getattr(task.request, "delivery_info", None)
    if not isinstance(delivery, dict):
        return None
    value = delivery.get("routing_key")
    return value if isinstance(value, str) else None


def _run_canary(task: Task, expected_queue: str) -> CanaryPayload:
    require_dispatch(task, f"canary-{expected_queue}")
    observed_queue = _observed_queue(task)
    if observed_queue is not None and observed_queue != expected_queue:
        raise CanaryConfigurationError("CANARY_WRONG_QUEUE")
    revision = release_revision()
    task_id = str(getattr(task.request, "id", None) or uuid.uuid4())
    checked_at = datetime.now(UTC)
    try:
        with SyncSessionLocal() as db:
            database_ok = db.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError as exc:
        raise CanaryConfigurationError("CANARY_DATABASE_UNAVAILABLE") from exc
    _outbox_contract_dry_run()
    payload: CanaryPayload = {
        "queue": expected_queue,
        "release_revision": revision,
        "cloud_run_revision": os.getenv("K_REVISION") or None,
        "task_id": task_id,
        "result": "ok",
        "checked_at": checked_at.isoformat(),
        "checks": {"database": database_ok, "redis": True, "outbox_dry_run": True},
    }
    try:
        with redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5
        ) as client:
            if not client.ping():
                raise CanaryConfigurationError("CANARY_REDIS_UNAVAILABLE")
            client.setex(
                canary_key(revision, expected_queue),
                CANARY_TTL_SECONDS,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
    except RedisError as exc:
        raise CanaryConfigurationError("CANARY_REDIS_UNAVAILABLE") from exc
    return payload


@celery_app.task(name="app.workers.canary_tasks.canary_default", bind=True)
def canary_default(task: Task) -> CanaryPayload:
    return _run_canary(task, "default")


@celery_app.task(name="app.workers.canary_tasks.canary_content", bind=True)
def canary_content(task: Task) -> CanaryPayload:
    return _run_canary(task, "content")


@celery_app.task(name="app.workers.canary_tasks.canary_sov", bind=True)
def canary_sov(task: Task) -> CanaryPayload:
    return _run_canary(task, "sov")


@celery_app.task(name="app.workers.canary_tasks.canary_reports", bind=True)
def canary_reports(task: Task) -> CanaryPayload:
    return _run_canary(task, "reports")


@celery_app.task(name="app.workers.canary_tasks.canary_leadgen", bind=True)
def canary_leadgen(task: Task) -> CanaryPayload:
    return _run_canary(task, "leadgen")


def read_queue_canaries(*, now: datetime | None = None) -> QueueCanaryFacts:
    observed_at = now or datetime.now(UTC)
    try:
        revision = release_revision()
    except CanaryConfigurationError:
        return QueueCanaryFacts(False, EXPECTED_QUEUES, None, {})
    results: dict[str, CanaryPayload] = {}
    missing: list[str] = []
    try:
        with redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5
        ) as client:
            for queue in EXPECTED_QUEUES:
                raw = client.get(canary_key(revision, queue))
                payload = _parse_payload(raw)
                if payload is None or not _is_current(payload, revision, queue, observed_at):
                    missing.append(queue)
                else:
                    results[queue] = payload
    except RedisError:
        return QueueCanaryFacts(False, EXPECTED_QUEUES, revision, {})
    return QueueCanaryFacts(not missing, tuple(missing), revision, results)


def _parse_payload(raw: bytes | None) -> CanaryPayload | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    queue = value.get("queue")
    revision = value.get("release_revision")
    cloud_revision = value.get("cloud_run_revision")
    task_id = value.get("task_id")
    result = value.get("result")
    checked_at = value.get("checked_at")
    checks = value.get("checks")
    if not (
        isinstance(queue, str)
        and isinstance(revision, str)
        and isinstance(task_id, str)
        and isinstance(result, str)
        and isinstance(checked_at, str)
    ):
        return None
    if cloud_revision is not None and not isinstance(cloud_revision, str):
        return None
    if not isinstance(checks, dict) or not all(
        isinstance(key, str) and isinstance(item, bool) for key, item in checks.items()
    ):
        return None
    return {
        "queue": queue,
        "release_revision": revision,
        "cloud_run_revision": cloud_revision,
        "task_id": task_id,
        "result": result,
        "checked_at": checked_at,
        "checks": checks,
    }


def _is_current(
    payload: CanaryPayload, revision: str, queue: str, observed_at: datetime
) -> bool:
    try:
        checked_at = datetime.fromisoformat(str(payload["checked_at"]))
    except (TypeError, ValueError):
        return False
    return (
        payload.get("queue") == queue
        and payload.get("release_revision") == revision
        and payload.get("result") == "ok"
        and payload.get("checks")
        == {"database": True, "redis": True, "outbox_dry_run": True}
        and checked_at.tzinfo is not None
        and timedelta(0) <= observed_at - checked_at <= CANARY_MAX_AGE
    )

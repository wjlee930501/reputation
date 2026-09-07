"""Durable, coalesced, bounded IndexNow delivery from committed OperationRun intents."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from celery import current_task
from sqlalchemy import and_, or_, select

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.operations import OperationRun, OperationRunState
from app.services import indexnow
from app.workers.dispatch_auth import require_dispatch

logger = logging.getLogger(__name__)

_CLAIM_BATCH: Final = 5
_LEASE_SECONDS: Final = 3 * 60
_MAX_ATTEMPTS: Final = 5
_RETRY_DELAYS: Final = (0, 60, 5 * 60, 30 * 60, 2 * 60 * 60)


@dataclass(frozen=True, slots=True)
class ClaimedIntent:
    run_id: uuid.UUID
    lease_owner: str
    base_url: str
    urls: tuple[str, ...]
    revision: str
    attempt_count: int
    claimed_version: int


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _retry_due(run: OperationRun, now: datetime) -> bool:
    state = getattr(run.state, "value", run.state)
    if run.attempt_count >= _MAX_ATTEMPTS:
        return False
    if state == OperationRunState.RUNNING.value:
        lease_expires_at = _utc(run.lease_expires_at)
        return lease_expires_at is not None and lease_expires_at <= now
    if state != OperationRunState.REQUESTED.value:
        return False
    last_attempt = _utc(run.heartbeat_at)
    if last_attempt is None:
        return True
    delay = _RETRY_DELAYS[min(run.attempt_count, len(_RETRY_DELAYS) - 1)]
    return last_attempt <= now - timedelta(seconds=delay)


def _due_predicate(now: datetime):
    requested_backoff = [
        and_(
            OperationRun.attempt_count == attempt,
            OperationRun.heartbeat_at
            <= now - timedelta(seconds=_RETRY_DELAYS[attempt]),
        )
        for attempt in range(_MAX_ATTEMPTS)
    ]
    return or_(
        and_(
            OperationRun.state == OperationRunState.REQUESTED.value,
            OperationRun.attempt_count < _MAX_ATTEMPTS,
            or_(OperationRun.heartbeat_at.is_(None), *requested_backoff),
        ),
        and_(
            OperationRun.state == OperationRunState.RUNNING.value,
            OperationRun.lease_expires_at.is_not(None),
            OperationRun.lease_expires_at <= now,
        ),
    )


def _fail_exhausted(run: OperationRun, now: datetime) -> None:
    run.state = OperationRunState.FAILED
    run.lease_owner = None
    run.lease_expires_at = None
    run.heartbeat_at = now
    run.failure_count = run.total_count
    run.completed_at = now
    run.safe_error_code = "INDEXNOW_RETRY_EXHAUSTED"
    run.safe_error_message = "색인 제출 신호를 정해진 횟수 안에 전달하지 못했습니다."
    run.version += 1


def _claim_due(now: datetime, lease_owner: str) -> tuple[list[ClaimedIntent], int]:
    claimed: list[ClaimedIntent] = []
    invalid = 0
    with SyncSessionLocal() as db:
        candidates = list(
            db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type == indexnow.INDEXNOW_OPERATION_TYPE,
                    _due_predicate(now),
                )
                .order_by(OperationRun.requested_at, OperationRun.id)
                .with_for_update(skip_locked=True)
                .limit(_CLAIM_BATCH * 5)
            )
            .scalars()
            .all()
        )
        for run in candidates:
            if (
                getattr(run.state, "value", run.state) == OperationRunState.RUNNING.value
                and run.attempt_count >= _MAX_ATTEMPTS
            ):
                _fail_exhausted(run, now)
                invalid += 1
                continue
            if len(claimed) >= _CLAIM_BATCH or not _retry_due(run, now):
                continue
            parsed = indexnow.parse_submission_intent(run)
            if parsed is None:
                run.state = OperationRunState.FAILED
                run.failure_count = run.total_count
                run.completed_at = now
                run.safe_error_code = "INDEXNOW_INTENT_INVALID"
                run.safe_error_message = "색인 제출 대상을 안전하게 확인할 수 없습니다."
                run.version += 1
                invalid += 1
                continue
            base_url, urls, revision = parsed
            run.state = OperationRunState.RUNNING
            run.started_at = run.started_at or now
            run.heartbeat_at = now
            run.lease_owner = lease_owner
            run.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
            run.attempt_count += 1
            run.version += 1
            claimed.append(
                ClaimedIntent(
                    run.id,
                    lease_owner,
                    base_url,
                    tuple(urls),
                    revision,
                    run.attempt_count,
                    run.version,
                )
            )
        db.commit()
    return claimed, invalid


def _coalesced_groups(intents: list[ClaimedIntent]) -> dict[str, tuple[list[str], list[ClaimedIntent]]]:
    grouped: dict[str, tuple[list[str], list[ClaimedIntent]]] = {}
    by_base: dict[str, list[ClaimedIntent]] = defaultdict(list)
    for intent in intents:
        by_base[intent.base_url].append(intent)
    for base_url, members in by_base.items():
        urls = sorted({url for member in members for url in member.urls})
        grouped[base_url] = (urls, members)
    return grouped


def _finish(
    intents: list[ClaimedIntent],
    *,
    succeeded: bool,
    retryable: bool,
    now: datetime,
) -> tuple[int, int]:
    completed = 0
    retrying = 0
    ids = [intent.run_id for intent in intents]
    if not ids:
        return completed, retrying
    owners = {intent.run_id: intent.lease_owner for intent in intents}
    versions = {intent.run_id: intent.claimed_version for intent in intents}
    with SyncSessionLocal() as db:
        runs = list(
            db.execute(select(OperationRun).where(OperationRun.id.in_(ids)).with_for_update())
            .scalars()
            .all()
        )
        for run in runs:
            if (
                getattr(run.state, "value", run.state) != OperationRunState.RUNNING.value
                or run.lease_owner != owners.get(run.id)
                or run.version != versions.get(run.id)
            ):
                continue
            run.lease_owner = None
            run.lease_expires_at = None
            run.heartbeat_at = now
            run.version += 1
            if succeeded:
                run.state = OperationRunState.SUCCEEDED
                run.success_count = run.total_count
                run.failure_count = 0
                run.completed_at = now
                run.safe_error_code = None
                run.safe_error_message = None
                run.result_summary = {"delivery": "accepted", "revision": str(run.request_payload.get("revision") or "")}
                completed += 1
            elif not retryable:
                run.state = OperationRunState.FAILED
                run.failure_count = run.total_count
                run.completed_at = now
                run.safe_error_code = "INDEXNOW_REJECTED"
                run.safe_error_message = "색인 제출 요청이 재시도할 수 없는 응답으로 거부됐습니다."
                completed += 1
            elif run.attempt_count >= _MAX_ATTEMPTS:
                run.state = OperationRunState.FAILED
                run.failure_count = run.total_count
                run.completed_at = now
                run.safe_error_code = "INDEXNOW_RETRY_EXHAUSTED"
                run.safe_error_message = "색인 제출 신호를 정해진 횟수 안에 전달하지 못했습니다."
                completed += 1
            else:
                run.state = OperationRunState.REQUESTED
                run.completed_at = None
                run.safe_error_code = "INDEXNOW_RETRY_SCHEDULED"
                run.safe_error_message = "색인 제출 신호를 자동으로 다시 전달할 예정입니다."
                retrying += 1
        db.commit()
    return completed, retrying


async def _submit(base_url: str, urls: list[str]) -> indexnow.SubmissionResult:
    return await indexnow.submit_urls_result(base_url=base_url, urls=urls)


@celery_app.task(
    name="app.workers.indexnow_retry.drain",
    soft_time_limit=120,
    time_limit=150,
)
def drain() -> dict[str, int | str]:
    """Drain committed intents without turning search-engine hints into operator alerts."""

    require_dispatch(current_task, "drain-indexnow-retries")
    if not indexnow.is_configured():
        return {"skipped": "not_configured", "claimed": 0, "submitted": 0}
    now = datetime.now(UTC)
    # A delivery retry may reuse the Celery task id. A fresh claim token prevents a late
    # completion from an expired attempt from finalizing a newer claim of the same row.
    lease_owner = f"indexnow:{uuid.uuid4()}"
    intents, invalid = _claim_due(now, lease_owner)
    submitted = 0
    retrying = 0
    terminal = invalid
    for base_url, (urls, members) in _coalesced_groups(intents).items():
        try:
            outcome = asyncio.run(_submit(base_url, urls))
        except Exception:  # noqa: BLE001 - bounded intent state owns delivery failure.
            logger.warning("IndexNow retry delivery failed unexpectedly: host=%s", base_url)
            outcome = indexnow.SubmissionResult(False, True, "unexpected_error")
        completed_count, retrying_count = _finish(
            members,
            succeeded=outcome.accepted,
            retryable=outcome.retryable,
            now=datetime.now(UTC),
        )
        terminal += completed_count
        retrying += retrying_count
        if outcome.accepted:
            submitted += len(urls)
    return {
        "claimed": len(intents),
        "submitted": submitted,
        "retrying": retrying,
        "terminal": terminal,
    }

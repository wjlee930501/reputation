"""Durable, bounded orchestration state for source evidence extraction."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import OperationRun, OperationRunState

SOURCE_PROCESSING_OPERATION = "SOURCE_EVIDENCE_PROCESSING"
SOURCE_PROCESSING_BATCH_SIZE = 1
SOURCE_PROCESSING_METADATA_KEY = "_source_processing"
SOURCE_PROCESSING_VERSION = 2
SOURCE_PROCESSING_RETRY_DELAY_SECONDS = 15 * 60


def normalized_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalized_source_value(field_name: str, value: Any) -> Any:
    """Normalize API patch values before deciding whether evidence became stale."""

    if field_name in {"title", "url", "raw_text", "operator_note"}:
        return normalized_optional_text(value)
    if field_name == "source_type":
        return getattr(value, "value", value)
    if field_name == "source_metadata":
        metadata = dict(value or {})
        metadata.pop(SOURCE_PROCESSING_METADATA_KEY, None)
        metadata.pop("extraction_input_hash", None)
        metadata.pop("extraction_coverage", None)
        return json.loads(json.dumps(metadata, sort_keys=True, ensure_ascii=False))
    return value


def source_patch_changed_fields(source: Any, update: dict[str, Any]) -> set[str]:
    return {
        field_name
        for field_name, value in update.items()
        if normalized_source_value(field_name, getattr(source, field_name, None))
        != normalized_source_value(field_name, value)
    }


def processing_input_hash(source: Any, content_hash: str) -> str:
    payload = {
        "version": SOURCE_PROCESSING_VERSION,
        "source_type": getattr(getattr(source, "source_type", None), "value", source.source_type),
        "content_hash": content_hash,
        "source_metadata": normalized_source_value(
            "source_metadata", getattr(source, "source_metadata", {})
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def processing_claim(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    value = (metadata or {}).get(SOURCE_PROCESSING_METADATA_KEY)
    return dict(value) if isinstance(value, dict) else None


def with_processing_claim(
    metadata: dict[str, Any] | None,
    *,
    token: str,
    input_hash: str,
    claimed_at: datetime,
) -> dict[str, Any]:
    result = dict(metadata or {})
    result[SOURCE_PROCESSING_METADATA_KEY] = {
        "token": token,
        "input_hash": input_hash,
        "claimed_at": claimed_at.astimezone(timezone.utc).isoformat(),
        "version": SOURCE_PROCESSING_VERSION,
    }
    return result


def without_processing_claim(metadata: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(metadata or {})
    result.pop(SOURCE_PROCESSING_METADATA_KEY, None)
    return result


def merge_source_metadata_patch(
    current: dict[str, Any] | None,
    requested: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep server-owned processing facts out of the client-controlled metadata object."""

    reserved_keys = (
        SOURCE_PROCESSING_METADATA_KEY,
        "extraction_input_hash",
        "extraction_coverage",
    )
    merged = {
        key: value for key, value in (requested or {}).items() if key not in reserved_keys
    }
    for key in reserved_keys:
        if key in (current or {}):
            merged[key] = (current or {})[key]
    return merged


def client_source_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Expose useful extraction coverage without leaking server claim/fencing state."""

    result = dict(metadata or {})
    result.pop(SOURCE_PROCESSING_METADATA_KEY, None)
    result.pop("extraction_input_hash", None)
    return result


def run_dispatch_matches(
    payload: dict[str, Any] | None,
    *,
    source_id: uuid.UUID | str,
    dispatch_token: str | None,
) -> bool:
    state = payload or {}
    return bool(
        dispatch_token
        and int(state.get("in_flight") or 0) == 1
        and state.get("in_flight_source_id") == str(source_id)
        and state.get("in_flight_dispatch_token") == dispatch_token
    )


def source_run_key(source_ids: Iterable[uuid.UUID | str]) -> str:
    identity = "\n".join(sorted(str(source_id) for source_id in source_ids))
    return "source-processing:" + hashlib.sha256(identity.encode()).hexdigest()


def source_processing_reservation_id(
    *,
    source_id: uuid.UUID | str,
    input_hash: str,
    dispatch_token: str | None,
    task_id: str,
    retry: int,
) -> str:
    """Identify one durable execution epoch, stable across message redelivery."""

    generation = dispatch_token or task_id
    return (
        f"source:{source_id}:{input_hash}:dispatch:{generation}:retry:{max(0, int(retry))}"
    )


async def create_or_get_source_processing_run(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    source_identities: list[str] | None = None,
) -> tuple[OperationRun, bool]:
    """Persist the full target snapshot before any broker publish."""

    base_key = source_run_key(source_identities or source_ids)
    existing = await db.scalar(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
            OperationRun.idempotency_key.startswith(base_key),
            OperationRun.state.in_(
                (
                    OperationRunState.REQUESTED,
                    OperationRunState.QUEUED,
                    OperationRunState.RUNNING,
                )
            ),
        )
    )
    if existing is not None:
        return existing, False
    prior = await db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == SOURCE_PROCESSING_OPERATION,
            OperationRun.idempotency_key.startswith(base_key),
        )
    )
    # A terminal run is immutable history. A source deliberately returned to
    # PENDING gets a new durable attempt while repeated requests reuse its active run.
    idempotency_key = base_key if prior is None else f"{base_key}:retry:{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    run = OperationRun(
        hospital_id=hospital_id,
        operation_type=SOURCE_PROCESSING_OPERATION,
        state=OperationRunState.REQUESTED,
        idempotency_key=idempotency_key,
        total_count=len(source_ids),
        request_payload={
            "source_ids": [str(source_id) for source_id in source_ids],
            "cursor": 0,
            "in_flight": 0,
            "batch_size": SOURCE_PROCESSING_BATCH_SIZE,
            "item_results": {},
        },
        requested_at=now,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run, True


def serialize_source_processing_run(run: OperationRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "state": str(getattr(run.state, "value", run.state)),
        "total_count": int(run.total_count or 0),
        "success_count": int(run.success_count or 0),
        "failure_count": int(run.failure_count or 0),
        "skipped_count": int(run.skipped_count or 0),
    }


async def prepare_next_source_run_item(
    db: AsyncSession, run_id: uuid.UUID
) -> tuple[OperationRun, str, str] | None:
    """Reserve exactly one next item so the broker backlog stays bounded."""

    run = await db.scalar(select(OperationRun).where(OperationRun.id == run_id).with_for_update())
    if run is None or str(getattr(run.state, "value", run.state)) in {
        OperationRunState.SUCCEEDED.value,
        OperationRunState.PARTIAL.value,
        OperationRunState.FAILED.value,
        OperationRunState.CANCELLED.value,
    }:
        return None
    payload = dict(run.request_payload or {})
    retry_at = payload.get("next_retry_at")
    if retry_at:
        try:
            if datetime.fromisoformat(str(retry_at)) > datetime.now(timezone.utc):
                return None
        except (TypeError, ValueError):
            pass
    if int(payload.get("in_flight") or 0) > 0:
        return None
    source_ids = [str(item) for item in payload.get("source_ids") or []]
    cursor = int(payload.get("cursor") or 0)
    if cursor >= len(source_ids):
        run.state = (
            OperationRunState.SUCCEEDED
            if int(run.failure_count or 0) == 0
            else OperationRunState.PARTIAL
        )
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return None
    source_id = source_ids[cursor]
    dispatch_token = str(uuid.uuid4())
    payload["cursor"] = cursor + 1
    payload["in_flight"] = 1
    payload["in_flight_source_id"] = source_id
    payload["in_flight_dispatch_token"] = dispatch_token
    payload.pop("next_retry_at", None)
    run.request_payload = payload
    run.state = OperationRunState.QUEUED
    run.queued_at = datetime.now(timezone.utc)
    run.safe_error_code = None
    run.safe_error_message = None
    await db.commit()
    return run, source_id, dispatch_token


async def release_source_run_dispatch(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    source_id: str,
    dispatch_token: str,
    error: str,
) -> None:
    run = await db.scalar(select(OperationRun).where(OperationRun.id == run_id).with_for_update())
    if run is None:
        return
    payload = dict(run.request_payload or {})
    source_ids = [str(item) for item in payload.get("source_ids") or []]
    cursor = int(payload.get("cursor") or 0)
    if (
        run_dispatch_matches(
            payload, source_id=source_id, dispatch_token=dispatch_token
        )
        and cursor > 0
        and source_ids[cursor - 1] == source_id
    ):
        payload["cursor"] = cursor - 1
        payload["in_flight"] = 0
        payload.pop("in_flight_source_id", None)
        payload.pop("in_flight_dispatch_token", None)
        run.request_payload = payload
    run.state = OperationRunState.REQUESTED
    run.safe_error_code = "BROKER_UNAVAILABLE"
    run.safe_error_message = error[:500]
    await db.commit()

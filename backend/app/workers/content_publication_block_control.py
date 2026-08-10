"""Durable retry run for content that is still blocked at publication time."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import JSONValue, OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload


def ensure_publication_block_run(
    db: Session,
    *,
    item: ContentItem,
    hospital: Hospital,
    code: str,
    message: str,
) -> OperationRun:
    """Return one retryable failed run for the current blocked content revision."""
    operation_type = (
        "REGENERATE_CONTENT_IMAGE" if code == "CONTENT_IMAGE_NOT_READY" else "REGENERATE_CONTENT"
    )
    idempotency_key = _idempotency_key(item, code)
    existing = db.execute(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital.id,
            OperationRun.operation_type == operation_type,
            OperationRun.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    now = datetime.now(UTC)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type=operation_type,
        state=OperationRunState.FAILED,
        idempotency_key=idempotency_key,
        request_payload=build_request_payload(
            DispatchPayload("content_item", str(item.id), "content", (str(item.id),))
        ),
        attempt_count=1,
        total_count=1,
        success_count=0,
        failure_count=1,
        skipped_count=0,
        result_summary={
            "items": {
                str(item.id): {
                    "state": "FAILED",
                    "safe_error_code": code,
                    "safe_error_message": message,
                }
            }
        },
        safe_error_code=code,
        safe_error_message=message,
        started_at=now,
        completed_at=now,
        version=1,
    )
    db.add(run)
    return run


def _idempotency_key(item: ContentItem, code: str) -> str:
    revision = getattr(item, "body_updated_at", None) or getattr(item, "generated_at", None)
    material: tuple[JSONValue, ...] = (
        str(item.id),
        code,
        str(getattr(item, "scheduled_date", "")),
        revision.isoformat() if isinstance(revision, datetime) else "unversioned",
        bool(getattr(item, "image_url", None)),
    )
    digest = hashlib.sha256(repr(material).encode()).hexdigest()[:24]
    return f"auto-publish-block:{item.id}:{digest}"

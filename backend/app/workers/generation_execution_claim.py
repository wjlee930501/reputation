"""Promote a queue reservation to a fenced execution lease, without provider IO.

The authenticated OperationRun claim proves ownership. A queued item's old TTL
alone does not invalidate it if nobody reassigned it. Rotating the item token at
execution start means a duplicate queued message cannot spend provider tokens.
A newer reservation or a different worker/version always wins.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models.content import ContentItem
from app.models.hospital import HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.workers.nightly_generation_batch import GENERATION_WRITE_BACK_STATUSES


def _human_edit_stamp(item):
    edited_at = item.human_edited_at
    if edited_at is None:
        return None
    if edited_at.tzinfo is None:
        edited_at = edited_at.replace(tzinfo=UTC)
    return edited_at.astimezone(UTC).isoformat()


def begin_generation_execution(db, item_id, queued_token, context, *, now=None):
    observed_at = now or datetime.now(UTC)
    run = db.execute(
        select(OperationRun)
        .where(
            OperationRun.id == context.run_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        db.rollback()
        return None
    payload = run.request_payload or {}
    expires = run.lease_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if not (
        run.operation_type == "GENERATE_CONTENT_ITEM"
        and run.state == OperationRunState.RUNNING
        and run.task_id == context.worker_id
        and run.lease_owner == context.worker_id
        and run.version == context.version
        and expires is not None
        and expires > observed_at
        and payload.get("source_type") == "content_item"
        and payload.get("source_id") == str(item_id)
    ):
        db.rollback()
        return None
    item = db.execute(
        select(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.hospital_id == run.hospital_id,
        )
        .options(joinedload(ContentItem.hospital))
        .with_for_update(of=ContentItem)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if not (
        item is not None
        and item.status in GENERATION_WRITE_BACK_STATUSES
        and item.hospital.status == HospitalStatus.ACTIVE
        and item.hospital.site_live
    ):
        db.rollback()
        return None
    previous = payload.get("generation_execution")
    previous = previous if isinstance(previous, dict) else {}
    previous_version = previous.get("run_version")
    valid_redelivery = (
        isinstance(previous_version, int)
        and not isinstance(previous_version, bool)
        and previous_version < context.version
        and previous.get("reservation_token") == str(queued_token)
        and previous.get("execution_token") == str(item.generation_claim_token)
        and previous.get("worker_id") == context.worker_id
        and previous.get("human_edited_at") == _human_edit_stamp(item)
    )
    if item.generation_claim_token != queued_token and not valid_redelivery:
        db.rollback()
        return None
    execution_token = uuid.uuid4()
    item.generation_claim_token = execution_token
    item.generation_claimed_at = observed_at
    # Broker arguments retain the reservation token. Persist its relationship
    # to this execution atomically; only a newer validated run claim may resume.
    run.request_payload = {
        **payload,
        "generation_execution": {
            "reservation_token": str(queued_token),
            "execution_token": str(execution_token),
            "run_version": context.version,
            "worker_id": context.worker_id,
            "human_edited_at": _human_edit_stamp(item),
        },
    }
    db.commit()
    return item, execution_token

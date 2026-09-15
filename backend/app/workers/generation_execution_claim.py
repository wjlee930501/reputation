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
        and item.generation_claim_token == queued_token
        and item.hospital.status == HospitalStatus.ACTIVE
        and item.hospital.site_live
    ):
        db.rollback()
        return None
    execution_token = uuid.uuid4()
    item.generation_claim_token = execution_token
    item.generation_claimed_at = observed_at
    db.commit()
    return item, execution_token

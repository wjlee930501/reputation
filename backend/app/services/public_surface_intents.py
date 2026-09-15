"""Transactional public-surface intent. No network, provider call, or commit.

The caller owns the hospital advisory lock and domain transaction. Existing
SITE_REVALIDATION reconciliation dispatches committed intents; a rolled-back
mutation leaves no work. Success means cache invalidation accepted, NOT verified
patient-visible delivery. That distinction must remain in operational reporting.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState


def enqueue_public_surface_intent(db, hospital, *, content_ids=()):
    # Real ORM entities only; route unit doubles do not imply persisted changes.
    if not isinstance(hospital, Hospital):
        return None
    ids = sorted({str(uuid.UUID(str(value))) for value in content_ids})
    now = datetime.now(UTC)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="SITE_REVALIDATION",
        state=OperationRunState.RUNNING,
        idempotency_key=f"public-surface:{hospital.id}:{uuid.uuid4()}",
        request_payload={"scope": "HOSPITAL", "content_ids": ids},
        result_summary={"invalidation_state": "PENDING", "page_visibility_verified": False},
        started_at=now,
        heartbeat_at=now - timedelta(seconds=60),
        total_count=1,
        attempt_count=0,
    )
    db.add(run)
    return run

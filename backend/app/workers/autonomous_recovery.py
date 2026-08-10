"""Recover committed workflow state whose first Celery dispatch was lost."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from celery import current_task
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services.site_revalidation_control import retry_delay
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch

_BATCH_SIZE: Final = 100


class RecoveryCounts(TypedDict):
    site_builds: int
    site_revalidations: int


def _now() -> datetime:
    return datetime.now(UTC)


def _revalidation_is_due(run: OperationRun, observed_at: datetime) -> bool:
    delay = retry_delay(run.attempt_count)
    if delay is None:
        return False
    last_attempt = run.heartbeat_at or run.started_at or run.requested_at
    return last_attempt <= observed_at - timedelta(seconds=delay)


@celery_app.task(name="app.workers.autonomous_recovery.reconcile")
def reconcile() -> RecoveryCounts:
    """Re-dispatch idempotent work from committed database truth."""

    require_dispatch(current_task, "reconcile-autonomous-workflows")
    observed_at = _now()
    with SyncSessionLocal() as db:
        hospitals = list(
            db.execute(
                select(Hospital)
                .where(
                    Hospital.profile_complete.is_(True),
                    Hospital.v0_report_done.is_(True),
                    Hospital.site_built.is_(False),
                )
                .order_by(Hospital.created_at, Hospital.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
        )
        runs = [
            run
            for run in db.execute(
                select(OperationRun)
                .where(
                    OperationRun.operation_type == "SITE_REVALIDATION",
                    OperationRun.state == OperationRunState.RUNNING,
                )
                .order_by(OperationRun.heartbeat_at, OperationRun.id)
                .with_for_update(skip_locked=True)
                .limit(_BATCH_SIZE)
            )
            .scalars()
            .all()
            if _revalidation_is_due(run, observed_at)
        ]

        for hospital in hospitals:
            hospital_id = str(hospital.id)
            celery_app.send_task(
                "app.workers.tasks.build_aeo_site",
                args=[hospital_id],
                queue="default",
                headers=build_dispatch_headers("build-aeo-site", hospital_id),
            )
        for run in runs:
            celery_app.send_task(
                "app.workers.tasks.retry_site_revalidation",
                args=[str(run.id), run.attempt_count],
                queue="default",
            )
            run.heartbeat_at = observed_at
        db.commit()
    return {"site_builds": len(hospitals), "site_revalidations": len(runs)}

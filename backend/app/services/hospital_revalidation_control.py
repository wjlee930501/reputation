"""Create durable recovery state for committed hospital-page cache changes."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import get_async_sessionmaker
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services.site_revalidation_control import (
    REVALIDATION_RETRY_DELAYS_SECONDS,
    RevalidationRetryPlan,
    _touch_incident,
    retry_delay,
)


async def start_hospital_revalidation_failure(
    slug: str,
) -> RevalidationRetryPlan | None:
    normalized_slug = slug.strip().lower()
    if not normalized_slug:
        return None
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        hospital = await db.scalar(
            select(Hospital).where(Hospital.slug == normalized_slug).with_for_update()
        )
        if hospital is None:
            return None
        mutation_version = hospital.updated_at or hospital.created_at or datetime.now(UTC)
        key = f"site-revalidation:hospital:{hospital.id}:{mutation_version.isoformat()}"
        existing = await db.scalar(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital.id,
                OperationRun.operation_type == "SITE_REVALIDATION",
                OperationRun.idempotency_key == key,
            )
        )
        if existing is not None:
            return RevalidationRetryPlan(
                existing.id,
                retry_delay(existing.attempt_count)
                if existing.state == OperationRunState.RUNNING.value
                else None,
                existing.state == OperationRunState.FAILED.value,
            )

        now = datetime.now(UTC)
        run = OperationRun(
            hospital_id=hospital.id,
            operation_type="SITE_REVALIDATION",
            state=OperationRunState.RUNNING.value,
            idempotency_key=key,
            request_payload={"scope": "HOSPITAL"},
            result_summary={"hospital_mutation_committed": True},
            safe_error_code="CACHE_REVALIDATION_FAILED",
            safe_error_message="공개 페이지에 최신 병원 정보가 아직 반영되지 않았습니다.",
            started_at=now,
            heartbeat_at=now,
            total_count=1,
        )
        db.add(run)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(
                select(OperationRun).where(
                    OperationRun.hospital_id == hospital.id,
                    OperationRun.operation_type == "SITE_REVALIDATION",
                    OperationRun.idempotency_key == key,
                )
            )
            return (
                RevalidationRetryPlan(existing.id, None, False)
                if existing is not None
                else None
            )
        await _touch_incident(db, run, terminal=False)
        await db.commit()
        return RevalidationRetryPlan(
            run.id,
            REVALIDATION_RETRY_DELAYS_SECONDS[0],
            False,
            True,
        )

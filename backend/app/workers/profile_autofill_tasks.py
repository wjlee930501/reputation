"""계약 등록 직후 병원 프로필 자동 입력 초안을 만들어 둔다(병원 행에는 쓰지 않는다)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import anyio
from celery.app.task import Task
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.celery_app import celery_app
from app.core.database import get_async_sessionmaker
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services import cost_guard
from app.services.audit_log import write_audit_log
from app.services.hospital_profile_autofill import autofill_profile
from app.workers.dispatch_auth import require_dispatch

PROFILE_AUTOFILL_OPERATION = "PROFILE_AUTOFILL"
PROFILE_AUTOFILL_REGISTRATION_KEY = "registration"
PROFILE_AUTOFILL_ACTOR = "system:profile-autofill"


def _existing_draft_stmt(hospital_id: uuid.UUID):
    return select(OperationRun).where(
        OperationRun.hospital_id == hospital_id,
        OperationRun.operation_type == PROFILE_AUTOFILL_OPERATION,
        OperationRun.idempotency_key == PROFILE_AUTOFILL_REGISTRATION_KEY,
    )


async def _autofill_once(hospital_id: uuid.UUID) -> dict[str, str]:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        hospital = await db.get(Hospital, hospital_id)
        if hospital is None:
            return {"status": "NOT_FOUND"}
        if await db.scalar(_existing_draft_stmt(hospital_id)) is not None:
            return {"status": "ALREADY_DONE"}
        name, website_url, blog_url = hospital.name, hospital.website_url, hospital.blog_url

    decision = await cost_guard.check_and_increment("content")
    if not decision.allowed:
        return {"status": "COST_BLOCKED"}
    result = await autofill_profile(
        name, website_url=website_url, blog_url=blog_url, hospital_id=hospital_id
    )
    sources = [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.sources]
    now = datetime.now(UTC)
    async with sessions() as db:
        run = OperationRun(
            hospital_id=hospital_id,
            operation_type=PROFILE_AUTOFILL_OPERATION,
            idempotency_key=PROFILE_AUTOFILL_REGISTRATION_KEY,
            state=OperationRunState.SUCCEEDED,
            total_count=1,
            success_count=1,
            attempt_count=1,
            request_payload={"trigger": "contract_registration"},
            result_summary={
                "draft": result.draft,
                "field_meta": result.field_meta,
                "violations": result.violations,
                "rejected_fields": result.rejected_fields,
                "naver_place_id": result.naver_place_id,
                "sources": sources,
            },
            requested_at=now,
            started_at=now,
            completed_at=now,
        )
        db.add(run)
        await write_audit_log(
            db,
            action="autofill_profile",
            hospital_id=hospital_id,
            actor=PROFILE_AUTOFILL_ACTOR,
            target_type="hospital",
            target_id=hospital_id,
            detail={
                "name": name,
                "filled_fields": sorted(result.draft.keys()),
                "violation_fields": [v["field"] for v in result.violations],
                "rejected_fields": [v["field"] for v in result.rejected_fields],
                "sources": sources,
                "stored_as_draft": True,
            },
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return {"status": "ALREADY_DONE"}
    return {"status": "DRAFT_STORED"}


@celery_app.task(
    name="app.workers.profile_autofill_tasks.autofill_registered_hospital_profile",
    bind=True,
    soft_time_limit=240,
    time_limit=300,
)
def autofill_registered_hospital_profile(self: Task, hospital_id: str) -> dict[str, str]:
    require_dispatch(self, "autofill-registered-profile", hospital_id)
    return anyio.run(_autofill_once, uuid.UUID(hospital_id))

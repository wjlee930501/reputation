"""Incident and outbox projection for monthly content-slot reconciliation failures."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_safety import sanitize_operator_text
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import (
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification


def _dedupe_key(hospital_id: uuid.UUID, period_key: str) -> str:
    return build_incident_key(
        "monthly_slots",
        "hospital_month",
        f"{hospital_id}:{period_key}",
        IncidentFingerprint.VALIDATION_FAILED,
    )


async def open_monthly_slot_failure(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    period_key: str,
    error_code: str,
) -> uuid.UUID:
    """Open one operator-visible incident and Slack outbox for a failed slot reconciliation."""

    observed_at = datetime.now(UTC)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = _dedupe_key(hospital_id, period_key)
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        previous_state = previous.state if previous is not None else None
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="monthly_slots",
                object_type="hospital_month",
                object_id=f"{hospital_id}:{period_key}",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                incident_type="MONTHLY_SLOT_GENERATION_FAILED",
                severity=IncidentSeverity.HIGH,
                customer_impact="다음 달 콘텐츠 슬롯 일부가 생성되지 않아 자동 운영 일정이 비게 됩니다.",
                source_type="MONTHLY_SLOT_GENERATION",
                next_action=(
                    "병원의 콘텐츠 스케줄에서 발행요일과 요금제가 월간 편수를 수용하는지 확인한 뒤 "
                    "다음 자동 복구 주기 결과를 확인하세요."
                ),
                admin_path=f"/hospitals/{hospital_id}/schedule",
                hospital_id=hospital_id,
                source_id=f"{hospital_id}:{period_key}",
                safe_error_code="MONTHLY_SLOT_GENERATION_FAILED",
                safe_error_message=sanitize_operator_text(error_code, limit=200),
            ),
            actor="monthly-slot-worker",
            reason="monthly slot reconciliation failed",
            now=observed_at,
        )
        if previous_state is None or previous_state in {
            IncidentState.RECOVERED.value,
            IncidentState.ACKNOWLEDGED.value,
        }:
            await enqueue_notification(
                db,
                build_open_incident_notification(
                    IncidentSlackProjection(
                        incident.id,
                        hospital_name,
                        incident.severity,
                        incident.customer_impact,
                        incident.next_action,
                        incident.admin_path,
                        "병원 운영 담당자",
                        "다음 달 시작 전",
                        incident.hospital_id,
                        incident.operation_run_id,
                        incident.version,
                        incident.safe_error_message or "월간 슬롯 생성에 실패했습니다.",
                        incident.episode_seq,
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


async def recover_monthly_slot_failure(*, hospital_id: uuid.UUID, period_key: str) -> bool:
    """Recover a prior monthly-slot incident after an observed successful reconciliation."""

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.scalar(
            select(Incident).where(
                Incident.dedupe_key == _dedupe_key(hospital_id, period_key),
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
        if incident is None:
            return False
        current = incident
        if current.state == IncidentState.OPEN.value:
            retrying = await mark_retrying(
                db,
                current.id,
                expected_version=current.version,
                actor="monthly-slot-worker",
                reason="monthly slot retry observed",
            )
            if not isinstance(retrying, Incident):
                await db.rollback()
                return False
            current = retrying
        recovered = await mark_recovered(
            db,
            current.id,
            expected_version=current.version,
            observed_success=True,
            actor="monthly-slot-worker",
            reason="monthly slot reconciliation succeeded",
        )
        await db.commit()
        return isinstance(recovered, Incident)

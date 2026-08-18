"""Operator-visible incidents for weekly SoV measurement blockers."""

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

_SOURCE_TYPE = "WEEKLY_SOV_MEASUREMENT"


def _dedupe_key(hospital_id: uuid.UUID, week_key: str) -> str:
    return build_incident_key(
        "weekly_sov",
        "hospital_week",
        f"{hospital_id}:{week_key}",
        IncidentFingerprint.VALIDATION_FAILED,
    )


async def open_weekly_sov_failure(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    week_key: str,
    error_code: str,
    operation_run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    observed_at = datetime.now(UTC)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = _dedupe_key(hospital_id, week_key)
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        previous_state = previous.state if previous is not None else None
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="weekly_sov",
                object_type="hospital_week",
                object_id=f"{hospital_id}:{week_key}",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                incident_type="WEEKLY_SOV_MEASUREMENT_FAILED",
                severity=IncidentSeverity.HIGH,
                customer_impact="주간 AI 노출 측정이 완료되지 않아 운영 판단과 월간 리포트 근거가 비게 됩니다.",
                source_type=_SOURCE_TYPE,
                next_action="측정 질문 설정, 비용 한도, 외부 측정 서비스 장애 여부를 확인한 뒤 주간 측정을 재시도하세요.",
                admin_path=f"/hospitals/{hospital_id}/reports",
                hospital_id=hospital_id,
                operation_run_id=operation_run_id,
                source_id=f"{hospital_id}:{week_key}",
                safe_error_code=sanitize_operator_text(error_code, limit=100),
                safe_error_message=_safe_message(error_code),
            ),
            actor="weekly-sov-worker",
            reason="weekly visibility measurement failed",
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
                        "이번 주 측정 마감 전",
                        incident.hospital_id,
                        incident.operation_run_id,
                        incident.version,
                        incident.safe_error_message or "주간 AI 검색 노출 측정에 실패했습니다.",
                        incident.episode_seq,
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


async def recover_weekly_sov_failure(*, hospital_id: uuid.UUID, week_key: str) -> bool:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.scalar(
            select(Incident).where(
                Incident.dedupe_key == _dedupe_key(hospital_id, week_key),
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
                actor="weekly-sov-worker",
                reason="weekly visibility measurement retry observed",
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
            actor="weekly-sov-worker",
            reason="weekly visibility measurement succeeded",
        )
        await db.commit()
        return isinstance(recovered, Incident)


def _safe_message(error_code: str) -> str:
    match error_code:
        case "WEEKLY_SOV_COST_GUARD_BLOCKED":
            return "비용 한도를 초과해 주간 AI 검색 노출 측정이 차단되었습니다."
        case "WEEKLY_SOV_NO_MEASUREMENT_MANIFEST":
            return "활성 측정 질문이나 측정 대상이 없어 이번 주 측정을 시작할 수 없습니다."
        case "WEEKLY_SOV_NO_PENDING_MEASUREMENTS":
            return "이번 주 측정에서 실행할 항목을 찾지 못했습니다."
        case "WEEKLY_SOV_UNRESOLVED_MANIFEST_STATE":
            return "이번 주 측정에 완료·제외·재측정 대상으로 분류되지 않은 항목이 남아 있습니다."
        case "WEEKLY_SOV_MEASUREMENT_POLICY_DRIFT":
            return "월간 측정 기준과 현재 실행 기준이 달라 외부 AI 측정 호출을 시작하지 않았습니다."
        case "WEEKLY_SOV_MEASUREMENT_PARTIAL":
            return "일부 AI 검색 서비스 측정이 실패해 이번 주 측정이 부분 완료 상태입니다."
        case _:
            return "주간 AI 검색 노출 측정이 완료되지 않았습니다."

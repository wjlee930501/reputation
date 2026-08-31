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


def _capacity_digest_key(week_key: str) -> str:
    return build_incident_key(
        "weekly_sov_capacity",
        "week",
        week_key,
        IncidentFingerprint.VALIDATION_FAILED,
    )


async def open_weekly_sov_capacity_digest(
    *, week_key: str, operation_run_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Collapse per-hospital HIGH-cap overflow into one durable weekly alert."""

    observed_at = datetime.now(UTC)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = _capacity_digest_key(week_key)
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        previous_state = previous.state if previous is not None else None
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="weekly_sov_capacity",
                object_type="week",
                object_id=week_key,
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                incident_type="SOV_HIGH_PRIORITY_CAP_EXCEEDED",
                severity=IncidentSeverity.HIGH,
                customer_impact=(
                    "한 곳 이상 병원의 높은 우선순위 질문 일부가 이번 주 측정에서 제외되었습니다."
                ),
                source_type=_SOURCE_TYPE,
                next_action="운영센터에서 이번 주 쿼리 타깃과 변형 수를 한 번 검토하세요.",
                admin_path="/operations",
                hospital_id=None,
                operation_run_id=operation_run_id,
                source_id=week_key,
                safe_error_code="SOV_HIGH_PRIORITY_CAP_EXCEEDED",
                safe_error_message=(
                    "주간 측정의 높은 우선순위 항목이 안전 상한을 넘은 병원이 있습니다."
                ),
            ),
            actor="weekly-sov-worker",
            reason="weekly high-priority capacity digest",
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
                        "주간 검색 노출 전체 병원",
                        incident.severity,
                        incident.customer_impact,
                        incident.next_action,
                        incident.admin_path,
                        "운영 담당자",
                        "이번 주 측정 마감 전",
                        None,
                        incident.operation_run_id,
                        incident.version,
                        incident.safe_error_message,
                        incident.episode_seq,
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


def _dedupe_key(hospital_id: uuid.UUID, period_key: str, *, monthly: bool = False) -> str:
    pipeline = "monthly_sov" if monthly else "weekly_sov"
    object_type = "hospital_month" if monthly else "hospital_week"
    return build_incident_key(
        pipeline,
        object_type,
        f"{hospital_id}:{period_key}",
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
    return await _open_sov_failure(
        hospital_id=hospital_id,
        hospital_name=hospital_name,
        period_key=week_key,
        error_code=error_code,
        operation_run_id=operation_run_id,
        monthly=False,
    )


async def open_monthly_sov_failure(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    period_key: str,
    error_code: str,
    operation_run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await _open_sov_failure(
        hospital_id=hospital_id,
        hospital_name=hospital_name,
        period_key=period_key,
        error_code=error_code,
        operation_run_id=operation_run_id,
        monthly=True,
    )


async def _open_sov_failure(
    *,
    hospital_id: uuid.UUID,
    hospital_name: str,
    period_key: str,
    error_code: str,
    operation_run_id: uuid.UUID | None,
    monthly: bool,
) -> uuid.UUID:
    observed_at = datetime.now(UTC)
    pipeline = "monthly_sov" if monthly else "weekly_sov"
    object_type = "hospital_month" if monthly else "hospital_week"
    incident_type = (
        "MONTHLY_SOV_MEASUREMENT_FAILED" if monthly else "WEEKLY_SOV_MEASUREMENT_FAILED"
    )
    period_label = "이번 달" if monthly else "이번 주"
    actor = "monthly-sov-worker" if monthly else "weekly-sov-worker"
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = _dedupe_key(hospital_id, period_key, monthly=monthly)
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        previous_state = previous.state if previous is not None else None
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline=pipeline,
                object_type=object_type,
                object_id=f"{hospital_id}:{period_key}",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                incident_type=incident_type,
                severity=IncidentSeverity.HIGH,
                customer_impact=(
                    f"{period_label} AI 노출 측정이 완료되지 않아 월간 리포트 근거가 비게 됩니다."
                ),
                source_type=("MONTHLY_SOV_MEASUREMENT" if monthly else _SOURCE_TYPE),
                next_action=(
                    "측정 질문 설정, 비용 한도, 외부 측정 서비스 장애 여부를 확인한 뒤 "
                    f"{period_label} 측정을 재시도하세요."
                ),
                admin_path=f"/hospitals/{hospital_id}/reports",
                hospital_id=hospital_id,
                operation_run_id=operation_run_id,
                source_id=f"{hospital_id}:{period_key}",
                safe_error_code=sanitize_operator_text(error_code, limit=100),
                safe_error_message=_safe_message(error_code),
            ),
            actor=actor,
            reason=f"{pipeline} visibility measurement failed",
            now=observed_at,
        )
        if not error_code.endswith("COST_GUARD_BLOCKED") and (
            previous_state is None
            or previous_state
            in {
                IncidentState.RECOVERED.value,
                IncidentState.ACKNOWLEDGED.value,
            }
        ):
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
                        f"{period_label} 측정 마감 전",
                        incident.hospital_id,
                        incident.operation_run_id,
                        incident.version,
                        incident.safe_error_message
                        or f"{period_label} AI 검색 노출 측정에 실패했습니다.",
                        incident.episode_seq,
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


async def recover_weekly_sov_failure(*, hospital_id: uuid.UUID, week_key: str) -> bool:
    return await _recover_sov_failure(hospital_id, week_key, monthly=False)


async def recover_monthly_sov_failure(*, hospital_id: uuid.UUID, period_key: str) -> bool:
    return await _recover_sov_failure(hospital_id, period_key, monthly=True)


async def _recover_sov_failure(
    hospital_id: uuid.UUID, period_key: str, *, monthly: bool
) -> bool:
    actor = "monthly-sov-worker" if monthly else "weekly-sov-worker"
    cadence = "monthly" if monthly else "weekly"
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.scalar(
            select(Incident).where(
                Incident.dedupe_key == _dedupe_key(hospital_id, period_key, monthly=monthly),
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
                actor=actor,
                reason=f"{cadence} visibility measurement retry observed",
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
            actor=actor,
            reason=f"{cadence} visibility measurement succeeded",
        )
        await db.commit()
        return isinstance(recovered, Incident)


def _safe_message(error_code: str) -> str:
    if error_code.startswith("MONTHLY_SOV_"):
        suffix = error_code.removeprefix("MONTHLY_SOV_")
        monthly_messages = {
            "COST_GUARD_BLOCKED": "비용 한도를 초과해 이번 달 AI 검색 노출 측정이 차단되었습니다.",
            "NO_MEASUREMENT_MANIFEST": "활성 고정 질문이나 측정 대상이 없어 이번 달 측정을 시작할 수 없습니다.",
            "UNRESOLVED_MANIFEST_STATE": "이번 달 측정에 완료·제외·재측정 대상으로 분류되지 않은 항목이 남아 있습니다.",
            "MEASUREMENT_POLICY_DRIFT": "동결한 측정 기준과 현재 실행 기준이 달라 외부 AI 측정 호출을 시작하지 않았습니다.",
            "MEASUREMENT_PARTIAL": "일부 AI 검색 서비스 측정이 실패해 이번 달 측정이 부분 완료 상태입니다.",
        }
        return monthly_messages.get(suffix, "이번 달 AI 검색 노출 측정이 완료되지 않았습니다.")
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

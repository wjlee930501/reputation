"""Pure response projections for the operations-center API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Literal

from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentState, NotificationOutbox, OperationRun
from app.schemas.operations import (
    OperationsAction,
    OperationsCustomer,
    OperationsHistoryEntry,
    OperationsOwner,
    OperationsQueue,
    OperationsQueueRow,
    OperationsRunSummary,
    OperationsSlackState,
)

__all__ = (
    "history",
    "next_onboarding_step",
    "owner_projection",
    "retry_action",
    "run_summary",
    "serialize_incident_row",
    "slack_state",
    "sla_state",
)

SlaState = Literal["NONE", "OVERDUE", "DUE"]
_RETRYABLE_RUN_STATES: Final = frozenset({"PARTIAL", "FAILED", "CANCELLED"})
_RETRYABLE_OPERATION_TYPES: Final = frozenset(
    {
        "TRIGGER_V0_REPORT",
        "RUN_SOV",
        "REBUILD_SITE",
        "GENERATE_MONTHLY_REPORT",
        "REGENERATE_CONTENT",
        "REGENERATE_CONTENT_IMAGE",
    }
)

_COST_LIMIT_CAUSE_CODES: Final = frozenset(
    {
        "COST_BLOCKED",
        "COST_GUARD_LIMIT_REACHED",
        "LEAD_DIAGNOSIS_COST_BLOCKED",
        "WEEKLY_SOV_COST_GUARD_BLOCKED",
    }
)
_COST_LIMIT_CAUSE_CODE: Final = "COST_LIMIT_EXHAUSTED"
_COST_LIMIT_CAUSE_MESSAGE: Final = (
    "오늘 설정된 AI 사용 한도가 소진되어 관련 자동 작업과 측정이 차단되었습니다."
)


def canonical_cause_code(code: str | None, incident_type: str) -> str:
    """Return a stable root-cause key shared by cost-limit symptoms."""
    normalized = (code or incident_type or "").strip().upper() or "OPERATION_FAILED"
    return _COST_LIMIT_CAUSE_CODE if normalized in _COST_LIMIT_CAUSE_CODES else normalized


def cause_message(code: str, stored_message: str | None, impact: str) -> str:
    """Return non-empty, operator-safe cause copy for an incident projection."""
    if code == _COST_LIMIT_CAUSE_CODE:
        return _COST_LIMIT_CAUSE_MESSAGE
    projected = (stored_message or impact or "").strip()
    return projected or "운영 작업이 완료되지 않은 원인을 확인해야 합니다."


def cost_guard_category(
    incident: Incident,
    run: OperationRun | None,
    cause_code: str,
) -> str | None:
    """Resolve the budget bucket behind a canonical cost-limit incident."""
    if cause_code != _COST_LIMIT_CAUSE_CODE:
        return None
    if incident.source_type == "COST_GUARD" and incident.source_id:
        category = incident.source_id.split(":", 1)[0].lower()
        if category in {"content", "image", "sov", "leadgen"}:
            return category
    context = " ".join(
        filter(
            None,
            (
                incident.incident_type,
                incident.source_type,
                run.operation_type if run is not None else None,
            ),
        )
    ).upper()
    if "LEAD" in context:
        return "leadgen"
    if any(token in context for token in ("SOV", "MEASUREMENT", "V0_REPORT")):
        return "sov"
    if "IMAGE" in context:
        return "image"
    return "content"


def owner_projection(user: AdminUser | None) -> OperationsOwner | None:
    """Project an optional assignee into the public operations contract."""
    if user is None:
        return None
    return OperationsOwner(id=user.id, name=user.name, email=user.email)


def sla_state(due_at: datetime | None, now: datetime) -> SlaState:
    """Classify a due time relative to the request clock."""
    if due_at is None:
        return "NONE"
    return "OVERDUE" if due_at < now else "DUE"


def history(incident: Incident) -> list[OperationsHistoryEntry]:
    """Serialize the operational incident timeline without fabricating events."""
    values = [OperationsHistoryEntry(event="OPENED", at=incident.first_seen_at)]
    if incident.last_seen_at != incident.first_seen_at:
        values.append(OperationsHistoryEntry(event="OCCURRED", at=incident.last_seen_at))
    if incident.recovered_at is not None:
        values.append(OperationsHistoryEntry(event="RECOVERED", at=incident.recovered_at))
    if incident.acknowledged_at is not None:
        values.append(OperationsHistoryEntry(event="ACKNOWLEDGED", at=incident.acknowledged_at))
    return values


def slack_state(outbox: NotificationOutbox | None) -> OperationsSlackState | None:
    """Project the latest Slack notification state when one is available."""
    if outbox is None:
        return None
    return OperationsSlackState(
        notification_id=outbox.id,
        notification_type=outbox.notification_type,
        state=outbox.state,
        attempt_count=outbox.attempt_count,
        max_attempts=outbox.max_attempts,
        next_attempt_at=outbox.next_attempt_at,
        sent_at=outbox.sent_at,
        safe_error_code=outbox.safe_error_code,
        safe_error_message=outbox.safe_error_message,
        version=outbox.version,
    )


def retry_action(hospital_id: uuid.UUID, run: OperationRun | None) -> OperationsAction | None:
    """Return the Admin BFF retry mutation descriptor only for supported failed runs."""
    if (
        run is None
        or run.state not in _RETRYABLE_RUN_STATES
        or run.operation_type not in _RETRYABLE_OPERATION_TYPES
    ):
        return None
    return OperationsAction(
        kind="RETRY_RUN",
        label="작업 다시 시도",
        method="POST",
        path=f"/api/admin/operations/hospitals/{hospital_id}/runs/{run.id}/retry",
        reason_required=True,
        requires_idempotency_key=True,
    )


def run_summary(hospital_id: uuid.UUID, run: OperationRun | None) -> OperationsRunSummary | None:
    """Project a durable operation run and its eligible retry affordance."""
    if run is None:
        return None
    return OperationsRunSummary(
        run_id=run.id,
        parent_run_id=run.parent_run_id,
        operation_type=run.operation_type,
        state=run.state,
        attempt_count=run.attempt_count,
        total_count=run.total_count,
        success_count=run.success_count,
        failure_count=run.failure_count,
        skipped_count=run.skipped_count,
        safe_error_code=run.safe_error_code,
        safe_error_message=run.safe_error_message,
        requested_at=run.requested_at,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        version=run.version,
        retry=retry_action(hospital_id, run),
    )


def serialize_incident_row(
    incident: Incident,
    hospital: Hospital | None,
    owner: AdminUser | None,
    run: OperationRun | None,
    outbox: NotificationOutbox | None,
    now: datetime,
    *,
    cause_group_key: str | None = None,
    same_type_count: int = 1,
    affected_hospital_count: int | None = None,
) -> OperationsQueueRow:
    """Build the operations queue projection for one incident and its related records."""
    hospital_id = incident.hospital_id
    customer_name = hospital.name if hospital is not None else "전체 시스템"
    customer_path = f"/hospitals/{hospital_id}" if hospital_id else "/operations"
    detail_path = (
        f"/operations/hospitals/{hospital_id}/incidents/{incident.id}"
        if hospital_id
        else f"/operations/incidents/{incident.id}"
    )
    stored_code = incident.safe_error_code or (run.safe_error_code if run is not None else None)
    projected_code = canonical_cause_code(stored_code, incident.incident_type)
    stored_message = incident.safe_error_message or (
        run.safe_error_message if run is not None else None
    )
    projected_message = cause_message(projected_code, stored_message, incident.customer_impact)
    budget_category = cost_guard_category(incident, run, projected_code)
    projected_group_key = cause_group_key or projected_code
    if budget_category is not None:
        projected_group_key = f"{projected_code}:{budget_category}"
    return OperationsQueueRow(
        id=f"incident:{incident.id}",
        queue=OperationsQueue.INCIDENTS,
        customer=OperationsCustomer(
            hospital_id=hospital_id, name=customer_name, admin_path=customer_path
        ),
        status=incident.state,
        severity=incident.severity,
        impact=incident.customer_impact,
        owner=owner_projection(owner),
        sla_due_at=incident.sla_due_at,
        sla_state=sla_state(incident.sla_due_at, now),
        next_action=incident.next_action,
        action=OperationsAction(
            kind="OPEN_INCIDENT", label="문제와 조치 확인", method="GET", path=detail_path
        ),
        retry=retry_action(hospital_id, run) if hospital_id else None,
        cause_code=projected_code,
        cause_message=projected_message,
        cause_group_key=projected_group_key,
        same_type_count=max(1, same_type_count),
        affected_hospital_count=(
            affected_hospital_count
            if affected_hospital_count is not None
            else (1 if hospital_id is not None else 0)
        ),
        cost_guard_category=budget_category,
        # RETRYING is automatic recovery in flight. Keep the row (the FE groups and
        # collapses it) but stop counting it as work waiting on a person.
        requires_operator_action=incident.state == IncidentState.OPEN.value,
        safe_cause=projected_message,
        history=history(incident),
        slack=slack_state(outbox),
        incident_id=incident.id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        occurred_at=incident.last_seen_at,
    )


def next_onboarding_step(hospital: Hospital) -> str:
    """Return the first incomplete gate in the hospital onboarding flow."""
    if not hospital.profile_complete:
        return "병원 기본 정보 탭에서 필수 병원 정보를 입력하고 저장하세요."
    if not hospital.v0_report_done:
        return "초기 진단 리포트 생성 결과를 확인하세요."
    if not hospital.site_built:
        return "콘텐츠 허브에 노출할 병원 공개 정보를 확인하세요."
    if not hospital.site_live:
        return "도메인 화면에서 공개 주소를 검증하고 운영 시작을 완료하세요."
    if not hospital.schedule_set:
        return (
            "온보딩 체크리스트에서 근거 자료 처리와 콘텐츠 운영 기준 자동 승인을 완료한 뒤 "
            "스케줄 탭에서 월간 콘텐츠 일정과 발행 요일을 저장하세요."
        )
    return "콘텐츠 운영 상태를 확인하고 첫 발행 준비를 진행하세요."

"""Pure response projections for the operations-center API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Literal

from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.hospital import Hospital
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
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
from app.services import published_image_recertification as recertification

__all__ = (
    "assign_action",
    "history",
    "incident_actions",
    "next_onboarding_step",
    "owner_projection",
    "resolve_action",
    "retry_action",
    "run_summary",
    "requires_operator_action",
    "serialize_incident_row",
    "slack_state",
    "sla_state",
)

SlaState = Literal["NONE", "OVERDUE", "DUE"]
_BFF_OPERATIONS_PREFIX: Final = "/api/admin/operations"
_RETRYABLE_RUN_STATES: Final = frozenset({"PARTIAL", "FAILED", "CANCELLED"})
_RETRYABLE_OPERATION_TYPES: Final = frozenset(
    {
        "TRIGGER_V0_REPORT",
        "RUN_SOV",
        "REBUILD_SITE",
        "GENERATE_MONTHLY_REPORT",
        "REGENERATE_CONTENT",
        "REGENERATE_CONTENT_IMAGE",
        "RECERTIFY_PUBLISHED_IMAGE",
    }
)
_SYSTEM_RETRY_CODES: Final = frozenset(
    {
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        "IMAGE_GENERATION_FAILED",
        "CONTENT_IMAGE_NOT_READY",
        "CONTENT_IMAGE_NOT_VERIFIED",
    }
)
_IMAGE_TERMINAL_CODES: Final = frozenset(
    {"IMAGE_GENERATION_RETRIES_EXHAUSTED", "CONTENT_IMAGE_POLICY_REJECTED"}
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
    cause_code: str,
    *,
    incident_type: str,
    source_type: str | None,
    source_id: str | None,
    run_operation_type: str | None,
) -> str | None:
    """Resolve the budget bucket behind a canonical cost-limit incident.

    Takes the five scalars it actually reads rather than the ORM rows, so the queue's
    cheap grouping pass can call it on a column projection instead of loading whole
    `Incident` objects just to throw them away.
    """
    if cause_code != _COST_LIMIT_CAUSE_CODE:
        return None
    if source_type == "COST_GUARD" and source_id:
        category = source_id.split(":", 1)[0].lower()
        if category in {"content", "image", "sov", "leadgen"}:
            return category
    context = " ".join(
        filter(None, (incident_type, source_type, run_operation_type))
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


def retry_action(
    hospital_id: uuid.UUID,
    run: OperationRun | None,
    *,
    enabled: bool = True,
) -> OperationsAction | None:
    """Return the Admin BFF retry mutation descriptor only for supported failed runs."""
    if (
        run is None
        or run.state not in _RETRYABLE_RUN_STATES
        or run.operation_type not in _RETRYABLE_OPERATION_TYPES
    ):
        return None
    if (
        run.operation_type == recertification.RECERTIFY_OPERATION
        and run.safe_error_code in recertification.OPERATOR_REQUIRED_CODES
    ):
        # 사람의 결정을 기다리는 차단이다. 다시 눌러도 같은 답을 유료로 사기만 한다 —
        # 남은 예산 검사는 재시도 라우트가 서버에서 한 번 더 한다.
        return None
    code = str(run.safe_error_code or "")
    if code in _SYSTEM_RETRY_CODES:
        return OperationsAction(
            kind="RETRY_RUN",
            label="시스템 재시도 중",
            method="POST",
            path=f"{_BFF_OPERATIONS_PREFIX}/hospitals/{hospital_id}/runs/{run.id}/retry",
            enabled=False,
            reason_required=False,
            requires_idempotency_key=True,
        )
    return OperationsAction(
        kind="RETRY_RUN",
        label="예산 소진·정책 거절" if code in _IMAGE_TERMINAL_CODES else "작업 다시 시도",
        method="POST",
        path=f"{_BFF_OPERATIONS_PREFIX}/hospitals/{hospital_id}/runs/{run.id}/retry",
        enabled=enabled,
        reason_required=True,
        requires_idempotency_key=True,
    )


def _incident_base_path(hospital_id: uuid.UUID | None, incident_id: uuid.UUID) -> str:
    """인시던트 변경 라우트의 공통 앞부분 — 라우터가 실제로 등록한 두 형태뿐이다.

    CAS 충돌 응답의 `incident_refetch_path`(`operations_center_actions`)와 같은 주소다.
    """
    if hospital_id is None:
        return f"{_BFF_OPERATIONS_PREFIX}/incidents/{incident_id}"
    return f"{_BFF_OPERATIONS_PREFIX}/hospitals/{hospital_id}/incidents/{incident_id}"


def _may_act(actor: AdminUser | None, incident: Incident) -> bool:
    """이 인시던트에 손댈 수 있는가 — 서버 가드와 같은 규칙.

    `require_owner`(전체 시스템 인시던트)·`require_assignee_or_owner`(병원 인시던트)와
    `authorize_run_retry`(연결 run의 담당자)가 모두 이 판정으로 수렴한다.

    화면의 버튼과 서버 인가가 갈리면 눌러야 알 수 있는 403이 된다.
    """
    if actor is None:
        return False
    if actor.role == ROLE_OWNER:
        return True
    return incident.hospital_id is not None and incident.owner_id == actor.id


def resolve_action(
    incident: Incident,
    run: OperationRun | None,
    *,
    actor: AdminUser | None,
) -> OperationsAction | None:
    """지금 이 인시던트가 받을 수 있는 상태 전이 하나.

    RETRYING은 복구 확인, RECOVERED는 문제 확인이다. 두 전이는 상태로 배타적이라 한
    자리면 충분하다. 복구 확인은 연결 작업의 성공이 관측돼야 서버가 받아 주므로
    (`_recover`의 `INCIDENT_RECOVERY_NOT_OBSERVED`) 여기서도 같은 조건을 본다.
    """
    if actor is None:
        return None
    base = _incident_base_path(incident.hospital_id, incident.id)
    allowed = _may_act(actor, incident)
    if incident.state == IncidentState.RETRYING.value:
        return OperationsAction(
            kind="RECOVER_INCIDENT",
            label="복구 확인 완료",
            method="POST",
            path=f"{base}/recover",
            enabled=allowed and run is not None and run.state == OperationRunState.SUCCEEDED,
            reason_required=True,
            requires_version=True,
        )
    if incident.state == IncidentState.RECOVERED.value:
        return OperationsAction(
            kind="ACK_INCIDENT",
            label="문제 확인 완료",
            method="POST",
            path=f"{base}/ack",
            enabled=allowed,
            reason_required=True,
            requires_version=True,
        )
    return None


def assign_action(incident: Incident, *, actor: AdminUser | None) -> OperationsAction | None:
    """담당 지정/변경(H-15). OWNER만 실행할 수 있다(`require_owner`)."""
    if actor is None:
        return None
    return OperationsAction(
        kind="ASSIGN_INCIDENT",
        label="담당 지정",
        method="POST",
        path=f"{_incident_base_path(incident.hospital_id, incident.id)}/assign",
        enabled=actor.role == ROLE_OWNER,
        reason_required=True,
        requires_version=True,
    )


def incident_actions(row: OperationsQueueRow) -> list[OperationsAction]:
    """행이 싣고 있는 실행 가능한 행동을 표시 순서대로 — 현황 예외 카드의 정본."""
    return [
        action
        for action in (row.action, row.retry, row.resolve, row.assign)
        if action is not None
    ]


def run_summary(
    hospital_id: uuid.UUID,
    run: OperationRun | None,
    *,
    retry_enabled: bool = True,
) -> OperationsRunSummary | None:
    """Project a durable operation run and its eligible retry affordance.

    화면은 행의 `retry`보다 이 자리의 `retry`를 우선한다. 그래서 요청자를 아는 호출은
    `authorize_run_retry`와 같은 판정(`run_retry_enabled`)을 넘겨야 한다 — 기본값을
    그대로 내보내면 상세 화면만 서버보다 관대해지고, 버튼은 눌러야 403을 알려준다.
    """
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
        retry=retry_action(hospital_id, run, enabled=retry_enabled),
    )


def requires_operator_action(state: str, sla_due_at: datetime | None, now: datetime) -> bool:
    """지금 사람이 손대야 풀리는 인시던트인가 — 큐 행·현황 카드·목록 건수의 유일한 정의.

    RETRYING is automatic recovery while its promised window remains. Once that
    deadline passes, the unresolved episode becomes operator work even though the
    last recorded transition still says retrying. 화면마다 상태 집합을 새로 쓰면
    자동 복구 중인 작업이 운영자의 할 일로 새어 나간다.
    """
    if state == IncidentState.OPEN.value:
        return True
    return (
        state == IncidentState.RETRYING.value and sla_due_at is not None and sla_due_at < now
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
    actor: AdminUser | None = None,
) -> OperationsQueueRow:
    """Build the operations queue projection for one incident and its related records.

    `actor`를 모르면 인가에 달린 행동(재시도 활성 여부·복구/확인·담당 지정)은 예전
    그대로 둔다 — 요청자를 모른 채 "가능하다"고 내보내면 화면이 서버보다 관대해진다.
    """
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
    budget_category = cost_guard_category(
        projected_code,
        incident_type=incident.incident_type,
        source_type=incident.source_type,
        source_id=incident.source_id,
        run_operation_type=run.operation_type if run is not None else None,
    )
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
        retry=(
            retry_action(
                hospital_id,
                run,
                # `authorize_run_retry`와 같은 규칙 — OWNER이거나 이 인시던트의 담당자.
                enabled=True if actor is None else _may_act(actor, incident),
            )
            if hospital_id
            else None
        ),
        resolve=resolve_action(incident, run, actor=actor),
        assign=assign_action(incident, actor=actor),
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
        requires_operator_action=requires_operator_action(
            incident.state, incident.sla_due_at, now
        ),
        safe_cause=projected_message,
        history=history(incident),
        slack=slack_state(outbox),
        incident_id=incident.id,
        operation_run_id=incident.operation_run_id,
        version=incident.version,
        occurred_at=incident.last_seen_at,
    )


def next_onboarding_step(hospital: Hospital) -> str:
    """Return the next operator-owned onboarding action.

    V0 is an independently recovering background diagnostic, so it never displaces
    a site, domain, or content-setup action that the operator can complete now.

    안내가 가리키는 곳은 지금 화면 구성 그대로여야 한다 — 사라진 허브·스케줄 탭·체크리스트
    이름을 남겨 두면 운영자가 없는 화면을 찾는다.
    """
    if not hospital.profile_complete:
        return "병원 정보 탭에서 필수 병원 정보를 입력하고 저장하세요."
    if not hospital.site_built:
        return "병원 공개 페이지에 노출할 병원 공개 정보를 확인하세요."
    if not hospital.site_live:
        return "병원 정보 화면의 자기 도메인에서 공개 주소를 검증하고 운영 시작을 완료하세요."
    if not hospital.schedule_set:
        return (
            "병원 정보 화면의 남은 필수 항목에서 근거 자료 처리와 콘텐츠 운영 기준 자동 "
            "승인을 완료한 뒤 콘텐츠 화면의 발행 요일에서 월간 콘텐츠 일정을 저장하세요."
        )
    return "콘텐츠 운영 상태를 확인하고 첫 발행 준비를 진행하세요."

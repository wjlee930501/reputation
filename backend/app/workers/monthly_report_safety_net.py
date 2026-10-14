"""월간 보고서 안전망 — 서비스 중인 모든 병원·월은 8일까지 전달 가능한 보고서를 갖는다(I2, W8).

8일 00:15 최종화가 끝난 뒤(최종 판정 시각 8일 09:00 KST부터) 지난달 서비스 구간에 든 병원마다
전달 게이트(`monthly_report_delivery_gate`)를 본다. 통과하지 못하면 사람을 부르지 않고 먼저 다시
만든다 — 원장 PDF만 문제면 숫자를 그대로 옮기는 템플릿 갱신, 그 밖(보고서 없음·측정 미완료로 남은
옛 판·차단)은 같은 달 재생성이다. 병원·월마다 2회(`REPAIR_BUDGET`), 6시간 간격(`REPAIR_SPACING`).
그래도 남을 때만 개발 대상 사고 하나(`MONTHLY_REPORT_SAFETY_NET_FAILED`)를 열고, 전달 가능해지면
같은 안전망이 닫는다. 원장 PDF 검증 실패의 자동 재생성(월간 PDF 대조 작업)도 같은 예산을 쓴다.

원장에게 보내는 일은 사람이 한다(D39). 여기서는 전달 가능한 artifact가 있게 할 뿐이다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentState, OperationRun, OperationRunState
from app.services import operation_run_payloads
from app.services.incident_types import IncidentFingerprint
from app.services.monthly_period import (
    MonthlyPeriodError,
    eligible_hospital_ids,
    monthly_final_judgment_at,
    prior_month_to_close,
)
from app.services.monthly_report_readiness import PeriodReportState, load_period_report_state
from app.services.ops_incident_alerts import open_ops_incident, recover_ops_incident

logger = logging.getLogger(__name__)

REPAIR_BUDGET = 2
REPAIR_SPACING = timedelta(hours=6)
# 이보다 오래 끝나지 않은 재생성 실행은 유실로 보고 예산 한 번을 쓴 것으로 센다(자율 복구가 따로 정리한다).
_IN_FLIGHT_HORIZON = timedelta(hours=2)
_REPAIR_KEY = "report-repair"
_REPORT_RUN_TYPES = ("GENERATE_MONTHLY_REPORT", "SCHEDULED_MONTHLY_REPORT")
_ACTIVE_STATES = (
    OperationRunState.REQUESTED,
    OperationRunState.QUEUED,
    OperationRunState.RUNNING,
)
# 원장 PDF만 문제인 판정 — 숫자는 그대로 두고 PDF만 다시 그리면 된다.
ARTIFACT_GATE_CODES = frozenset({"doctor_artifact_missing", "doctor_artifact_invalid"})

SAFETY_NET_INCIDENT_TYPE = "MONTHLY_REPORT_SAFETY_NET_FAILED"
_PIPELINE = "monthly_report_safety_net"
_OBJECT_TYPE = "hospital_period"
_SOURCE_TYPE = "MONTHLY_REPORT_SAFETY_NET"
_ACTOR = "monthly-report-safety-net"


@dataclass(frozen=True, slots=True)
class RepairDecision:
    action: str  # dispatched | in_flight | waiting | exhausted
    run_id: uuid.UUID | None = None
    template_only: bool = False


def _period_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def repair_runs(db: Session, hospital_id: uuid.UUID, year: int, month: int) -> list[OperationRun]:
    """그 병원·월의 안전망 재생성 시도 — 키의 시도 번호 순서(시계와 무관)."""
    prefix = f"{_REPAIR_KEY}:{hospital_id}:{_period_key(year, month)}:"
    runs = db.scalars(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type == "GENERATE_MONTHLY_REPORT",
            OperationRun.idempotency_key.startswith(prefix),
        )
    ).all()
    return sorted(runs, key=lambda run: _attempt_number(run.idempotency_key, prefix))


def _attempt_number(key: str | None, prefix: str) -> int:
    tail = str(key or "")[len(prefix):]
    return int(tail) if tail.isdigit() else 0


def _report_run_in_flight(db: Session, hospital_id: uuid.UUID, now: datetime) -> bool:
    """같은 병원의 보고서 생성이 아직 돌고 있으면 겹쳐 사지 않는다(버전 잠금·템플릿 갱신이 서로 막는다)."""
    runs = db.scalars(
        select(OperationRun).where(
            OperationRun.hospital_id == hospital_id,
            OperationRun.operation_type.in_(_REPORT_RUN_TYPES),
            OperationRun.state.in_(_ACTIVE_STATES),
        )
    ).all()
    return any(
        (_aware(run.created_at) or now) > now - _IN_FLIGHT_HORIZON for run in runs
    )


def dispatch_report_repair(
    db: Session,
    hospital: Hospital,
    year: int,
    month: int,
    *,
    prefer_template: bool,
    now: datetime | None = None,
) -> RepairDecision:
    """그 달 보고서를 한 번 더 만든다 — 병원·월마다 `REPAIR_BUDGET`회, `REPAIR_SPACING` 간격.

    첫 시도는 `prefer_template`이면 템플릿 갱신(숫자 그대로, PDF만 다시)이고, 두 번째는 같은 달
    재생성이다 — 템플릿 갱신이 숫자 판정으로 거절돼도 다음 시도가 다른 길로 간다. 커밋은 여기서 한다.
    """
    observed = now or datetime.now(timezone.utc)
    if _report_run_in_flight(db, hospital.id, observed):
        return RepairDecision("in_flight")
    runs = repair_runs(db, hospital.id, year, month)
    if len(runs) >= REPAIR_BUDGET:
        return RepairDecision("exhausted")
    if runs and (_aware(runs[-1].created_at) or observed) > observed - REPAIR_SPACING:
        return RepairDecision("waiting")
    attempt = len(runs) + 1
    template_only = prefer_template and attempt == 1
    run = _start_repair_run(db, hospital, year, month, attempt, template_only)
    return RepairDecision("dispatched", run.id, template_only)


def _start_repair_run(
    db: Session,
    hospital: Hospital,
    year: int,
    month: int,
    attempt: int,
    template_only: bool,
) -> OperationRun:
    from app.workers import tasks as worker_tasks
    from app.workers.dispatch_auth import build_dispatch_headers

    hospital_id = str(hospital.id)
    # generate_monthly_report_for_hospital(hospital_id, year, month, rebuild, automatic_recovery,
    # template_only, allow_recovery_pending) — 템플릿 갱신은 저장된 숫자를 옮기므로 측정 관문을 넘는다.
    task_args = (hospital_id, year, month, True, True, template_only, template_only)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.REQUESTED,
        idempotency_key=f"{_REPAIR_KEY}:{hospital_id}:{_period_key(year, month)}:{attempt}",
        requested_by_id=None,
        task_id=str(uuid.uuid4()),
        attempt_count=0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        request_payload=operation_run_payloads.build_request_payload(
            operation_run_payloads.DispatchPayload("hospital", hospital_id, "reports", task_args)
        ),
        result_summary={"period_year": year, "period_month": month},
        version=1,
    )
    db.add(run)
    db.commit()
    try:
        worker_tasks.generate_monthly_report_for_hospital.apply_async(
            args=list(task_args),
            queue="reports",
            headers={
                **build_dispatch_headers("generate-monthly-report", hospital_id),
                "operation_run_id": str(run.id),
            },
            task_id=run.task_id,
        )
    except Exception:  # noqa: BLE001 - REQUESTED run is redispatched by autonomous recovery.
        logger.exception(
            "Monthly report repair dispatch failed; autonomous recovery will retry",
            extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
        )
        return run
    worker_tasks._mark_weekly_sov_operation_queued(db, run.id, datetime.now(timezone.utc))
    return run


def _prefers_template(state: PeriodReportState) -> bool:
    return state.report is not None and state.gate.code in ARTIFACT_GATE_CODES


def run_monthly_report_safety_net(db: Session, *, now: datetime | None = None) -> dict[str, int | str]:
    """지난달 서비스 구간 병원마다 전달 가능한 보고서를 보장한다. 최종 판정 시각 전에는 아무것도 안 한다."""

    observed = now or datetime.now(timezone.utc)
    try:
        period = prior_month_to_close(observed)
    except MonthlyPeriodError:
        return {"status": "period_not_closed"}
    if observed < monthly_final_judgment_at(period.year, period.month):
        return {"status": "before_final_judgment"}
    period_key = _period_key(period.year, period.month)
    hospital_ids = list(eligible_hospital_ids(db, period))
    hospitals = (
        list(db.scalars(select(Hospital).where(Hospital.id.in_(hospital_ids))).all())
        if hospital_ids
        else []
    )
    open_incidents = {
        incident.hospital_id
        for incident in db.scalars(
            select(Incident).where(
                Incident.incident_type == SAFETY_NET_INCIDENT_TYPE,
                Incident.source_type == _SOURCE_TYPE,
                Incident.source_id.like(f"%:{period_key}"),
                Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
            )
        ).all()
    }
    counts = {"ready": 0, "dispatched": 0, "waiting": 0, "opened": 0, "recovered": 0}
    for hospital in sorted(hospitals, key=lambda row: (row.name, str(row.id))):
        state = load_period_report_state(db, hospital.id, period.year, period.month)
        object_id = f"{hospital.id}:{period_key}"
        if state.gate.ready:
            counts["ready"] += 1
            if hospital.id in open_incidents and _recover(hospital, object_id):
                counts["recovered"] += 1
            continue
        decision = dispatch_report_repair(
            db,
            hospital,
            period.year,
            period.month,
            prefer_template=_prefers_template(state),
            now=observed,
        )
        if decision.action == "dispatched":
            counts["dispatched"] += 1
        elif decision.action == "exhausted":
            if hospital.id not in open_incidents:
                _open(hospital, object_id, period_key, state)
                counts["opened"] += 1
        else:
            counts["waiting"] += 1
    db.commit()
    return {"status": "completed", "period": period_key, **counts}


def _open(hospital: Hospital, object_id: str, period_key: str, state: PeriodReportState) -> None:
    reason = state.gate.message or "전달 가능한 원장 보고서를 확인하지 못했습니다."
    try:
        anyio.run(
            lambda: open_ops_incident(
                pipeline=_PIPELINE,
                object_type=_OBJECT_TYPE,
                object_id=object_id,
                incident_type=SAFETY_NET_INCIDENT_TYPE,
                safe_error_code="MONTHLY_REPORT_SAFETY_NET_EXHAUSTED",
                problem=(
                    f"{period_key} 월간 보고서를 자동으로 두 번 다시 만들었지만 아직 전달할 수 "
                    f"없습니다: {reason}"
                ),
                customer_impact="이 병원의 지난달 원장 보고서를 아직 전달할 수 없습니다.",
                next_action=(
                    "개발팀이 보고서 생성 기록과 원인을 확인합니다. 해결되면 이 알림은 자동으로 닫힙니다."
                ),
                source_type=_SOURCE_TYPE,
                hospital_name=hospital.name,
                hospital_id=hospital.id,
                admin_path=f"/hospitals/{hospital.id}/reports",
                fingerprint=IncidentFingerprint.UNKNOWN,
                actor=_ACTOR,
            )
        )
    except Exception:  # noqa: BLE001 - next tick retries; the net never blocks other hospitals.
        logger.exception(
            "Monthly report safety-net incident could not be opened",
            extra={"hospital_id": str(hospital.id), "period": period_key},
        )


def _recover(hospital: Hospital, object_id: str) -> bool:
    try:
        return anyio.run(
            lambda: recover_ops_incident(
                pipeline=_PIPELINE,
                object_type=_OBJECT_TYPE,
                object_id=object_id,
                fingerprint=IncidentFingerprint.UNKNOWN,
                hospital_name=hospital.name,
                actor=_ACTOR,
                reason="전달 가능한 월간 보고서 확인",
            )
        )
    except Exception:  # noqa: BLE001 - next tick retries.
        logger.exception(
            "Monthly report safety-net incident could not be recovered",
            extra={"hospital_id": str(hospital.id)},
        )
        return False

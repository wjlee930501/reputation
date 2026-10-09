"""이미 해결된 일을 가리키는 열린 사고를 DB 근거로 닫는다(2026-10-02).

사고는 대개 실패한 그 대상·그 주·그 기간에 묶여, 다음 주 측정이나 다음 기간이 정상으로
지나가도 아무도 닫지 않았다. 일일 운영 요약이 8월 사고까지 '확인 필요'로 세며 쌓였다.

종류마다 '해결됐다'는 근거 하나를 정의한다. 근거가 없으면 그대로 둔다 — 아직 진행 중인
문제(예: 측정 기준 불일치로 계속 막힌 주간 측정, 검증이 맞지 않는 원장 PDF)는 열려 있어야
한다. 자동 재시도 중(RETRYING)인 사고는 자동화의 것이라 건드리지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    LeadDiagnosis,
    LeadReportArtifact,
    ReportStatus,
)
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
    OperationRun,
    OperationRunState,
)
from app.models.report import MonthlyReport
from app.services.notification_transport import CHANNEL_UNAVAILABLE_CODE, safe_error_message
from app.workers.task_incident_control import _audit, _transition_incident

BACKLOG_INCIDENT_BATCH = 50
_KST = ZoneInfo("Asia/Seoul")

Resolver = Callable[[Session, Incident, datetime], str | None]


def _measurement_period(incident: Incident) -> str | None:
    source_id = str(incident.source_id or "")
    _scope, separator, period = source_id.rpartition(":")
    return period if separator and period else None


def _same_period_measurement(
    db: Session, incident: Incident, _now: datetime
) -> str | None:
    if incident.hospital_id is None:
        return None
    period = _measurement_period(incident)
    if period is None:
        return None
    summary_field = (
        "measurement_month"
        if incident.incident_type == "MONTHLY_SOV_MEASUREMENT_FAILED"
        else "measurement_week"
    )
    succeeded = db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == incident.hospital_id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.state == OperationRunState.SUCCEEDED.value,
            OperationRun.requested_at > incident.first_seen_at,
            or_(
                OperationRun.result_summary[summary_field].as_string() == period,
                OperationRun.idempotency_key.endswith(f":{period}"),
            ),
        ).limit(1)
    )
    return "same_period_measurement_succeeded" if succeeded is not None else None


def _iso_week_end(label: str) -> date | None:
    try:
        year, week = label.split("-W")
        return date.fromisocalendar(int(year), int(week), 7)
    except (ValueError, TypeError):
        return None


def _weekly_capacity(db: Session, incident: Incident, now: datetime) -> str | None:
    if incident.hospital_id is not None:
        return _same_period_measurement(db, incident, now)
    # 전체 주간 용량 초과는 그 주가 끝나면 손쓸 것이 없다. 다음 주에도 넘치면 새 주의 사고가 열린다.
    week_end = _iso_week_end(str(incident.source_id or ""))
    if week_end is not None and now.astimezone(_KST).date() > week_end:
        return "measurement_week_ended"
    return None


def _budget_period_end(scope: str) -> date | None:
    parts = scope.split(":")
    try:
        period, value = parts[1], parts[2]
        if period == "daily":
            return datetime.strptime(value, "%Y%m%d").date()
        if period == "monthly":
            first = datetime.strptime(value, "%Y%m").date()
            return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    except (IndexError, ValueError):
        return None
    return None


def _budget_period(_db: Session, incident: Incident, now: datetime) -> str | None:
    period_end = _budget_period_end(str(incident.source_id or ""))
    if period_end is not None and now.astimezone(_KST).date() > period_end:
        return "budget_period_ended"
    return None


def _v0_report_created(db: Session, incident: Incident, _now: datetime) -> str | None:
    if incident.hospital_id is None:
        return None
    report = db.scalar(
        select(MonthlyReport.id).where(
            MonthlyReport.hospital_id == incident.hospital_id,
            MonthlyReport.report_type == "V0",
            MonthlyReport.created_at > incident.last_seen_at,
        ).limit(1)
    )
    return "v0_report_created" if report is not None else None


def _content_published(db: Session, incident: Incident, _now: datetime) -> str | None:
    item = db.get(ContentItem, incident.source_id) if incident.source_id else None
    if item is not None and item.first_published_at and item.first_published_at > incident.first_seen_at:
        return "content_published"
    return None


_FINAL_OUTBOX_STATES = ("SENT", "FAILED")


def _channel_dead_while(channel: object, observed_at: object):
    """그 알림의 채널에 '수신 불명'이 관측된 시각을 덮는, 이미 복구된 채널 사고가 있다."""

    channel_incident = aliased(Incident)
    return (
        select(channel_incident.id)
        .where(
            channel_incident.incident_type == "NOTIFICATION_DELIVERY_FAILED",
            channel_incident.source_type == "NOTIFICATION_OUTBOX",
            channel_incident.source_id == channel,
            channel_incident.state.in_(
                (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
            ),
            channel_incident.first_seen_at <= observed_at,
            channel_incident.recovered_at >= observed_at,
        )
        .exists()
    )


def _delivery_unknown_evidence(incident_source_id: object, observed_at: object):
    """수신 불명 사고를 닫을 근거가 있는 outbox 행. 해석기(`_redirected_delivery`)와 같은 규칙이다."""

    status = NotificationOutbox.provider_response["http_status"].as_integer()
    return (
        select(NotificationOutbox.id)
        .where(
            cast(NotificationOutbox.id, String) == incident_source_id,
            or_(
                NotificationOutbox.state.in_(_FINAL_OUTBOX_STATES),
                and_(status >= 300, status <= 399),
                _channel_dead_while(
                    func.coalesce(
                        NotificationOutbox.provider_response["channel_used"].as_string(),
                        NotificationOutbox.channel,
                    ),
                    observed_at,
                ),
            ),
        )
        .exists()
    )


def _hand_back_undelivered(row: NotificationOutbox, now: datetime) -> None:
    """전달되지 않은 것이 확실한 보류 행을 '보낼 채널 없음' 보류로 옮긴다.

    수신 불명 사고만 닫고 행을 그대로 두면 아무도 그 알림을 받지 못한 채 묻힌다. 이 표시로
    옮기면 발송기가 채널이 살아 있을 때 다시 보내거나, 이미 해결된 사고·24시간이 지난 알림은
    다시 보내지 않고 종결한다(`notification_channel_health.requeue_channel_held`).
    """

    if row.state != NotificationOutboxState.HOLD.value:
        return
    row.safe_error_code = CHANNEL_UNAVAILABLE_CODE
    row.safe_error_message = safe_error_message(CHANNEL_UNAVAILABLE_CODE)
    row.version += 1
    row.updated_at = now


def _redirected_delivery(db: Session, incident: Incident, now: datetime) -> str | None:
    # 302는 Slack이 받지 않았다는 뜻이다. '수신 여부 확인'이 아니라 웹훅 설정 오류이며,
    # 그 설정 오류는 채널 단위 사고 하나가 맡는다(`notification_channel_health`).
    row = db.get(NotificationOutbox, incident.source_id) if incident.source_id else None
    if row is None:
        return None
    # 사람이 다시 보내 결국 전달됐거나(SENT) 실패로 끝났다(FAILED는 전송 실패 사고가 맡는다).
    if row.state in _FINAL_OUTBOX_STATES:
        return "delivery_reached_final_state"
    status = (row.provider_response or {}).get("http_status")
    if isinstance(status, int) and 300 <= status <= 399:
        _hand_back_undelivered(row, now)
        return "redirect_was_not_delivered"
    # 그 채널이 죽어 있던 동안 관측된 불명은 전달되지 않은 것이다. 채널이 복구됐으면 닫는다.
    # 실제로 보낸 채널(대체 전송이면 다른 채널)로 맞추고, 그 기록이 없는 옛 행은 논리 채널로 본다.
    channel_used = (row.provider_response or {}).get("channel_used") or row.channel
    if db.scalar(select(_channel_dead_while(channel_used, incident.first_seen_at))):
        _hand_back_undelivered(row, now)
        return "channel_was_dead_and_recovered"
    return None


def _lead_diagnosis_gone(
    db: Session, incident: Incident
) -> tuple[LeadDiagnosis | None, str | None]:
    """복구 사고가 가리키는 그 진단과, 축과 무관하게 복구할 것이 없다는 근거.

    병원으로 전환되지 않은 리드의 사고는 같은 진단의 다음 복구 성공(RETRYING에서만 닫힘)만
    기다렸다. 아래 셋은 HTTP 복구 경계와 워커 claim이 모두 거절하는 상태라 누구도 이 사고를
    처리할 수 없다(`lead_diagnosis_tasks._claim_for_execution`·보고서 claim).
    - 행이 없다: 리드와 함께 지워졌다. 복구할 대상 자체가 없다.
    - PURGED: 개인정보 파기로 리포트와 질의 원문이 지워졌다. 되살릴 수 없고 되살려서도 안 된다.
    - 갈음됐다: AE가 값을 고쳐 새 진단을 만들었다. 갈음 경로는 그 순간 열린 사고만 닫으므로,
      이미 큐에 있던 복구가 갈음 뒤 claim을 잃고 다시 연 사고는 여기서만 닫힌다.
    리드의 병원 전환 여부(`converted_hospital_id`)는 보지 않는다 — 그 공백이 이 규칙의 이유다.
    """

    # `open_or_touch_incident`가 정규화해 저장한 값(`lead_diagnosis` → `LEAD_DIAGNOSIS`).
    if incident.source_type != "LEAD_DIAGNOSIS":
        return None, None
    try:
        diagnosis_id = uuid.UUID(str(incident.source_id))
    except ValueError:
        return None, None  # 알 수 없는 참조는 근거가 아니다. 열어 둔다.
    # 식별자 맵이 아니라 DB의 현재 행을 읽는다 — 지워진 행을 메모리 사본으로 '있다'고 보지 않는다.
    diagnosis = db.scalar(
        select(LeadDiagnosis)
        .where(LeadDiagnosis.id == diagnosis_id)
        .execution_options(populate_existing=True)
    )
    if diagnosis is None:
        return None, "lead_diagnosis_missing"
    if diagnosis.report_status == ReportStatus.PURGED.value:
        return diagnosis, "lead_diagnosis_purged"
    if diagnosis.superseded_at is not None:
        return diagnosis, "lead_diagnosis_superseded"
    return diagnosis, None


def _lead_measurement_recovered(db: Session, incident: Incident, _now: datetime) -> str | None:
    diagnosis, gone = _lead_diagnosis_gone(db, incident)
    if gone is not None or diagnosis is None:
        return gone
    # 폴러나 다른 경로가 그 진단의 측정을 리포트 가능한 상태로 끝냈다. 여전히 FAILED인 활성
    # 진단은 사람이 복구를 다시 걸어야 할 수 있으므로 나이와 무관하게 열어 둔다.
    if diagnosis.execution_status in REPORTABLE_EXECUTION_STATUSES:
        return "lead_measurement_succeeded"
    return None


def _lead_report_failure_anchor(db: Session, incident: Incident) -> datetime:
    """이 사고의 실패를 낸 복구가 요청된 시각. 이보다 나중에 만든 산출물만 그 복구를 대신한다.

    산출물의 `created_at`은 DB `now()`, 곧 그 생성 트랜잭션의 시작 시각(claim 직후·렌더 전)이다.
    그래서 실패 관측 시각(`last_seen_at`)과 비교하면, 진행 중인 생성에 밀려 거절된 복구(같은
    기대 시도 수로 요청됐다가 claim을 잃음)가 그 생성이 끝낸 리포트를 '이전 것'으로 본다.
    복구 요청은 진단 행 잠금 아래 그때의 시도 수를 읽으므로, 요청 뒤에 시작된 생성만 그 복구가
    하려던 일을 해낸 것이다. READY에서 다시 만들다 실패한 경우는 기존 산출물이 요청보다 먼저라
    근거가 되지 않는다.
    사고가 가리키는 실행이 마지막 실패를 낸 그 실행일 때만 쓴다 — 종료 기록(`completed_at`)이
    마지막 관측보다 앞서면 더 나중의 실패가 실행 참조 없이 기록된 것이다. 그 밖에는 실패
    관측 시각을 쓴다(덜 닫는 쪽).
    워커가 HTTP 처리기의 투영보다 먼저 실패해 처리기가 '이미 끝난 복구가 실패했다'로 다시
    기록하면(`mark_lead_recovery_started`) `last_seen_at`이 `completed_at`을 넘어 관측 시각으로
    돌아간다. 그때는 claim을 잃은 경합이 닫히지 않고 OPEN으로 남는다(안전한 쪽).
    """

    observed = incident.last_seen_at
    if incident.operation_run_id is None:
        return observed
    run = db.get(OperationRun, incident.operation_run_id)
    if (
        run is None
        or run.operation_type != incident.incident_type
        or (run.request_payload or {}).get("source_id") != incident.source_id
        or run.completed_at is None
        or run.completed_at < observed
    ):
        return observed
    return min(run.requested_at, observed)


def _lead_report_recovered(db: Session, incident: Incident, _now: datetime) -> str | None:
    diagnosis, gone = _lead_diagnosis_gone(db, incident)
    if gone is not None or diagnosis is None:
        return gone
    # 측정 성공은 리포트 축의 근거가 아니다. READY 표시만으로도 부족하다 — 실제로 서빙할 수
    # 있는(파기되지 않은) 산출물이 이 실패 뒤에 만들어져야 리포트가 다시 만들어진 것이다.
    # 복구는 READY도 다시 만들고, 그 재생성이 실패해도 진단은 기존 산출물로 READY에 돌아온다
    # (`_build_lead_report`). 그 기존 산출물은 근거가 아니다. BLOCKED는 열어 둔다.
    if diagnosis.report_status != ReportStatus.READY.value:
        return None
    servable = db.scalar(
        select(LeadReportArtifact.id).where(
            LeadReportArtifact.diagnosis_id == diagnosis.id,
            LeadReportArtifact.purged_at.is_(None),
            LeadReportArtifact.created_at > _lead_report_failure_anchor(db, incident),
        ).limit(1)
    )
    return "lead_report_ready" if servable is not None else None


def _monthly_cohort_enrolled(db: Session, incident: Incident, _now: datetime) -> str | None:
    # 사람이 질문·기록을 갖추면 다음 측정 때 자동 편입된다 — 편입이 곧 해결의 근거다.
    if incident.hospital_id is None:
        return None
    hospital = db.get(Hospital, incident.hospital_id)
    return "enrolled_in_monthly_cohort" if hospital is not None and hospital.monthly_sov_cohort else None


RESOLVERS: dict[str, Resolver] = {
    "MONTHLY_SOV_COHORT_GAP": _monthly_cohort_enrolled,
    "WEEKLY_SOV_MEASUREMENT_FAILED": _same_period_measurement,
    "MONTHLY_SOV_MEASUREMENT_FAILED": _same_period_measurement,
    "SOV_HIGH_PRIORITY_CAP_EXCEEDED": _weekly_capacity,
    "COST_GUARD_LIMIT_REACHED": _budget_period,
    "V0_REPORT_FAILED": _v0_report_created,
    "CONTENT_GENERATION_FAILED": _content_published,
    "NOTIFICATION_DELIVERY_UNKNOWN": _redirected_delivery,
    "RECOVER_LEAD_MEASUREMENT": _lead_measurement_recovered,
    "RECOVER_LEAD_REPORT": _lead_report_recovered,
}


def close_resolved_backlog_incidents(
    db: Session, *, limit: int = BACKLOG_INCIDENT_BATCH, now: datetime | None = None
) -> int:
    """근거가 있는 열린 사고를 조용히 닫는다. Slack은 보내지 않는다. 커밋은 호출자가 한다."""

    observed = now or datetime.now(UTC)
    candidates = list(
        db.execute(
            select(Incident)
            .where(
                Incident.state == IncidentState.OPEN.value,
                Incident.incident_type.in_(tuple(RESOLVERS)),
                # 수신 불명 사고는 수십 건이 근거 없이 쌓일 수 있다 — 닫을 근거가 있는 것만 집어
                # 배치 앞자리를 근거 없는 오래된 사고가 차지하지 않게 한다.
                or_(
                    Incident.incident_type != "NOTIFICATION_DELIVERY_UNKNOWN",
                    _delivery_unknown_evidence(Incident.source_id, Incident.first_seen_at),
                ),
            )
            .order_by(Incident.last_seen_at, Incident.id)
            .with_for_update(skip_locked=True)
            .limit(limit * 4)
        )
        .scalars()
        .all()
    )
    closed = 0
    for incident in candidates:
        if closed >= limit:
            break
        reason = RESOLVERS[incident.incident_type](db, incident, observed)
        if reason is None:
            continue
        retrying = _transition_incident(
            db, incident, expected_state=IncidentState.OPEN, next_state=IncidentState.RETRYING
        )
        if retrying is None:
            continue
        recovered = _transition_incident(
            db,
            retrying,
            expected_state=IncidentState.RETRYING,
            next_state=IncidentState.RECOVERED,
            recovered=True,
        )
        if recovered is None:
            continue
        acknowledged = _transition_incident(
            db,
            recovered,
            expected_state=IncidentState.RECOVERED,
            next_state=IncidentState.ACKNOWLEDGED,
            acknowledged=True,
        )
        _audit(
            db,
            acknowledged or recovered,
            "incident_resolved_by_later_evidence",
            detail_extra={"evidence": reason, "slack_suppressed": True},
        )
        closed += 1
    return closed

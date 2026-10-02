"""이미 해결된 일을 가리키는 열린 사고를 DB 근거로 닫는다(2026-10-02).

사고는 대개 실패한 그 대상·그 주·그 기간에 묶여, 다음 주 측정이나 다음 기간이 정상으로
지나가도 아무도 닫지 않았다. 일일 운영 요약이 8월 사고까지 '확인 필요'로 세며 쌓였다.

종류마다 '해결됐다'는 근거 하나를 정의한다. 근거가 없으면 그대로 둔다 — 아직 진행 중인
문제(예: 측정 기준 불일치로 계속 막힌 주간 측정, 검증이 맞지 않는 원장 PDF)는 열려 있어야
한다. 자동 재시도 중(RETRYING)인 사고는 자동화의 것이라 건드리지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    OperationRun,
    OperationRunState,
)
from app.models.report import MonthlyReport
from app.workers.task_incident_control import _audit, _transition_incident

BACKLOG_INCIDENT_BATCH = 50
_KST = ZoneInfo("Asia/Seoul")

Resolver = Callable[[Session, Incident, datetime], str | None]


def _later_weekly_measurement(db: Session, incident: Incident, _now: datetime) -> str | None:
    if incident.hospital_id is None:
        return None
    succeeded = db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == incident.hospital_id,
            OperationRun.operation_type == "RUN_SOV",
            OperationRun.state == OperationRunState.SUCCEEDED.value,
            OperationRun.requested_at > incident.last_seen_at,
        ).limit(1)
    )
    return "later_weekly_measurement_succeeded" if succeeded is not None else None


def _iso_week_end(label: str) -> date | None:
    try:
        year, week = label.split("-W")
        return date.fromisocalendar(int(year), int(week), 7)
    except (ValueError, TypeError):
        return None


def _weekly_capacity(db: Session, incident: Incident, now: datetime) -> str | None:
    if incident.hospital_id is not None:
        return _later_weekly_measurement(db, incident, now)
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


def _redirected_delivery(db: Session, incident: Incident, _now: datetime) -> str | None:
    # 302는 Slack이 받지 않았다는 뜻이다. '수신 여부 확인'이 아니라 웹훅 설정 오류이며,
    # 그 설정 오류는 채널 단위 사고 하나가 맡는다(`notification_delivery`).
    row = db.get(NotificationOutbox, incident.source_id) if incident.source_id else None
    status = (row.provider_response or {}).get("http_status") if row is not None else None
    if isinstance(status, int) and 300 <= status <= 399:
        return "redirect_was_not_delivered"
    return None


RESOLVERS: dict[str, Resolver] = {
    "WEEKLY_SOV_MEASUREMENT_FAILED": _later_weekly_measurement,
    "SOV_HIGH_PRIORITY_CAP_EXCEEDED": _weekly_capacity,
    "COST_GUARD_LIMIT_REACHED": _budget_period,
    "V0_REPORT_FAILED": _v0_report_created,
    "CONTENT_GENERATION_FAILED": _content_published,
    "NOTIFICATION_DELIVERY_UNKNOWN": _redirected_delivery,
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

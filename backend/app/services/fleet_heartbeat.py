"""Zero-provider daily fleet summary, separate from intervention alerts."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import aliased

from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentState, OperationRun
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.services.contract_delivery_coverage import (
    FleetContractCoverage,
    collect_contract_delivery_coverage,
)
from app.services.incident_types import notification_channel_for_incident_type
from app.services.notification_contracts import NotificationIntent, SlackMessage, validate_message
from app.services.notification_copy import incident_copy
from app.services.notification_milestone_rendering import safe_text
from app.services.operator_action import requires_operator_action
from app.services.pipeline_watchdog import KST, WatchdogReport
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate


@dataclass(frozen=True)
class FleetActionGroup:
    hospital_name: str
    incident_type: str
    count: int


@dataclass(frozen=True)
class FleetFacts:
    hospitals: int
    recovering: int
    operator_work: int
    failed_runs: int
    measured_hospitals: int
    recent_reports: int
    unresolved_failed_runs: int | None = None
    contract_coverage: FleetContractCoverage | None = None
    action_groups: tuple[FleetActionGroup, ...] = ()


def collect_fleet_facts(db, *, now):
    hospitals = int(
        db.scalar(
            select(func.count())
            .select_from(Hospital)
            .where(publicly_operational_hospital_predicate())
        )
        or 0
    )
    # Group deadlines instead of loading incident bodies or hospital names.
    recovering = operator_work = 0
    action_groups = []
    overdue = Incident.sla_due_at < now
    for state, past_due, name, kind, count in db.execute(
        select(
            Incident.state,
            overdue,
            Hospital.name,
            Incident.incident_type,
            func.count(),
        )
        .select_from(Incident)
        .outerjoin(Hospital, Hospital.id == Incident.hospital_id)
        .where(Incident.state.in_([IncidentState.OPEN, IncidentState.RETRYING]))
        .group_by(Incident.state, overdue, Hospital.name, Incident.incident_type)
    ):
        deadline = now - timedelta(microseconds=1) if past_due else None
        if requires_operator_action(state, deadline, now):
            operator_work += count
            action_groups.append(FleetActionGroup(name or "시스템", kind or "", count))
        else:
            recovering += count
    failed = int(
        db.scalar(
            select(func.count())
            .select_from(OperationRun)
            .where(
                OperationRun.state.in_(["FAILED", "PARTIAL"]),
                OperationRun.updated_at >= now - timedelta(days=1),
            )
        )
        or 0
    )
    # This is recent successful answer evidence, not complete monthly coverage.
    measured = (
        select(SovRecord.hospital_id)
        .join(Hospital)
        .where(
            publicly_operational_hospital_predicate(),
            SovRecord.measured_at.between(now - timedelta(days=35), now),
            # Same confirmed-answer contract as sov_engine.record_is_confirmed.
            func.upper(func.coalesce(SovRecord.measurement_status, "SUCCESS")) == "SUCCESS",
            or_(SovRecord.mention_verdict.is_(None), SovRecord.mention_verdict != "AMBIGUOUS"),
            SovRecord.is_mentioned.is_not(None),
            SovRecord.ai_platform.in_(["chatgpt", "gemini"]),
        )
        .group_by(SovRecord.hospital_id)
        .having(func.count(func.distinct(SovRecord.ai_platform)) == 2)
    )
    measured_count = int(db.scalar(select(func.count()).select_from(measured.subquery())) or 0)
    reports = int(
        db.scalar(
            select(func.count())
            .select_from(MonthlyReport)
            .where(
                MonthlyReport.created_at.between(now - timedelta(days=35), now),
            )
        )
        or 0
    )
    newer = aliased(OperationRun)
    # Historical failures are accounted for only through explicit incident/retry
    # lineage. Another month's report or another article is not recovery proof.
    accounted_incident = exists(
        select(Incident.id).where(Incident.operation_run_id == OperationRun.id)
    )
    later_success = exists(
        select(newer.id).where(
            newer.parent_run_id == OperationRun.id,
            newer.hospital_id.is_not_distinct_from(OperationRun.hospital_id),
            newer.operation_type == OperationRun.operation_type,
            newer.state == "SUCCEEDED",
            newer.completed_at >= func.coalesce(OperationRun.completed_at, OperationRun.updated_at),
        )
    )
    unresolved = int(db.scalar(
        select(func.count()).select_from(OperationRun).where(
            OperationRun.state.in_(["FAILED", "PARTIAL"]),
            OperationRun.updated_at >= now - timedelta(days=1),
            ~accounted_incident,
            ~later_success,
        )
    ) or 0)
    return FleetFacts(hospitals, recovering, operator_work, failed, measured_count, reports, unresolved, collect_contract_delivery_coverage(db, now=now), tuple(sorted(action_groups, key=lambda item: (-item.count, item.hospital_name, item.incident_type))))


def build_fleet_heartbeat(report: WatchdogReport, facts: FleetFacts, *, now, admin_base_url):
    fresh = timedelta(0) <= now - report.observed_at <= timedelta(minutes=15)
    unknown = (
        not fresh
        or not report.database_available
        or not report.redis_available
        or not report.publish_checked
        or report.publish_due_remaining is None
        or report.publish_published_today is None
        or facts.measured_hospitals < facts.hospitals
        or bool(facts.contract_coverage and facts.contract_coverage.unknown_schedule_hospitals)
    )
    degraded = (
        not report.queue_canaries_current
        or not report.beat_alive
        or report.generation_batch_stale
        or bool(report.publish_due_remaining)
        or (
            facts.unresolved_failed_runs
            if facts.unresolved_failed_runs is not None
            else facts.failed_runs
        ) > 0
        or facts.operator_work > 0
        or bool(facts.contract_coverage and facts.contract_coverage.delivery_at_risk)
    )
    # A known outage/action must not disappear behind missing measurement data.
    state = "미완료 작업 있음" if degraded else "일부 상태 미확인" if unknown else "점검 이상 없음"
    if facts.operator_work:
        state += " · 담당자 확인 필요"
    elif facts.recovering:
        state += " · 자동 복구 진행"

    def number(value):
        return "미확인" if value is None else str(value)

    coverage = facts.contract_coverage
    local_day = now.astimezone(KST).date().isoformat()
    title = f"[일일 요약] {local_day} · {state}"
    actions = []
    for group in facts.action_groups[:6]:
        copy = incident_copy(group.incident_type)
        role = "개발 담당" if notification_channel_for_incident_type(group.incident_type) == "SLACK_DEV" else "운영 담당"
        actions.append(f"• {safe_text(group.hospital_name, 70)} — {copy.title} {group.count}건 ({role})\n  {copy.action}")
    shown_count = sum(item.count for item in facts.action_groups[:6])
    if facts.operator_work > shown_count:
        actions.append(f"그 외 확인할 이슈 {facts.operator_work - shown_count}건은 운영센터에서 볼 수 있습니다.")
    if not actions:
        actions.append("지금 사람이 처리할 것으로 분류된 이슈는 없습니다.")
    if coverage and coverage.missing_allocations:
        actions.append(f"미배정 {coverage.missing_allocations}편은 일정 복구 대상입니다. 복구 실패가 확정되면 별도 조치 알림을 보냅니다.")
    if coverage and coverage.unknown_schedule_hospitals:
        actions.append(f"계약 일정이 없는 {coverage.unknown_schedule_hospitals}곳은 병원 콘텐츠에서 일정 등록 여부를 확인해 주세요.")
    actions.append(f"자동 복구 중 {facts.recovering}건은 시스템이 처리합니다. 수동으로 반복 실행하지 마세요.")
    state_lines = [f"공개 운영 {facts.hospitals}곳 · 확인 필요 이슈 {facts.operator_work}건",
        f"오늘 08시 이후 발행 기록 {number(report.publish_published_today)}편 · 예정일이 지난 미발행 {number(report.publish_due_remaining)}편"]
    if coverage is not None:
        state_lines += [f"이번 달 원 계약: 약정 {coverage.expected}편 / 배정 {coverage.allocated}편 / 최초 발행 {coverage.first_published}편",
            f"미배정 {coverage.missing_allocations}편 · 취소 미충족 {coverage.cancelled_deficit}편 · 기한 지난 미발행 {coverage.overdue_unpublished}편 · 다음 달 이월 미발행 {coverage.carried_out_unpublished}편"]
        if coverage.unknown_schedule_hospitals:
            state_lines.append(f"계약 일정 미확인 {coverage.unknown_schedule_hospitals}곳")
    state_lines += [f"최근 35일 양 서비스의 확정 답변이 있는 병원 {facts.measured_hospitals}/{facts.hospitals}곳 (월간 측정 완료 수는 아님)",
        "최초 발행은 현재 공개 건수와 다릅니다. 실제 공개 화면·보고서 전달 여부는 각 병원 화면에서 확인합니다."]
    if facts.unresolved_failed_runs:
        state_lines.append(f"복구 근거가 아직 없는 작업 실패 {facts.unresolved_failed_runs}건 — 개발 담당 확인 필요")
    details = "\n".join(state_lines)
    todo = "\n".join(actions)
    url = admin_base_url.rstrip("/") + "/operations"
    text = title + "\n" + details + "\n지금 할 일\n" + todo
    message = SlackMessage(text, (
        {"type": "header", "block_id": "fleet_header", "text": {"type": "plain_text", "text": title}},
        {"type": "section", "block_id": "fleet_daily_action_groups", "text": {"type": "plain_text", "text": todo}},
        {"type": "section", "block_id": "fleet_daily_facts", "text": {"type": "plain_text", "text": details}},
        {"type": "actions", "block_id": "fleet_daily_action", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "담당할 항목 확인"}, "url": url}]},
    ), url)
    validate_message(message, allowed_admin_base_url=admin_base_url)
    return NotificationIntent(
        dedupe_key=f"FLEET_HEARTBEAT:{local_day}",
        notification_type="FLEET_HEARTBEAT",
        message=message,
    )

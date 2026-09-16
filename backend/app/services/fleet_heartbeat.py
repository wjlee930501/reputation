"""Zero-provider daily fleet summary, separate from intervention alerts."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import aliased

from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentState, OperationRun
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.services.notification_contracts import NotificationIntent, SlackMessage
from app.services.operator_action import requires_operator_action
from app.services.pipeline_watchdog import KST, WatchdogReport
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate


@dataclass(frozen=True)
class FleetFacts:
    hospitals: int
    recovering: int
    operator_work: int
    failed_runs: int
    measured_hospitals: int
    recent_reports: int
    unresolved_failed_runs: int | None = None


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
    overdue = Incident.sla_due_at < now
    for state, past_due, count in db.execute(
        select(
            Incident.state,
            overdue,
            func.count(),
        )
        .where(Incident.state.in_([IncidentState.OPEN, IncidentState.RETRYING]))
        .group_by(Incident.state, overdue)
    ):
        deadline = now - timedelta(microseconds=1) if past_due else None
        if requires_operator_action(state, deadline, now):
            operator_work += count
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
    return FleetFacts(hospitals, recovering, operator_work, failed, measured_count, reports, unresolved)


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
    )
    # A known outage/action must not disappear behind missing measurement data.
    state = "미완료 항목 있음" if degraded else "관측 부족" if unknown else "관측 지표 양호"
    if facts.operator_work:
        state += " · 개입 필요"
    elif facts.recovering:
        state += " · 자동 복구 진행"

    def number(value):
        return "미확인" if value is None else str(value)

    local_day = now.astimezone(KST).date().isoformat()
    text = "\n".join(
        [
            f"{local_day} GEO 운영 요약 · {state}",
            f"공개 운영 병원 {facts.hospitals}곳",
            f"발행: 남은 예정 {number(report.publish_due_remaining)}건 / 오늘 08시 이후 DB 발행 {number(report.publish_published_today)}건",
            "예정 잔여와 오늘 발행은 서로 다른 집계이며 합계가 당일 약정 수량은 아닙니다.",
            f"자동 복구 {facts.recovering}건 · 사람의 개입 {facts.operator_work}건 · 최근 24시간 실패/부분 완료 이력 {facts.failed_runs}건",
            f"최근 35일 양 플랫폼 성공 답변 기록: {facts.measured_hospitals}/{facts.hospitals}곳 · 생성 보고서 {facts.recent_reports}건",
            "측정 전체 완료와 보고서 전달 준비는 운영 화면에서 확인합니다. DB 발행은 실제 페이지 관측 증명이 아닙니다.",
            "ChatGPT/Gemini 검색 API 답변 관측이며 소비자 화면 노출이나 검색 순위를 보장하지 않습니다.",
        ]
    )
    url = admin_base_url.rstrip("/") + "/operations"
    message = SlackMessage(
        text,
        (
            {
                "type": "section",
                "block_id": "fleet_daily_facts",
                "text": {"type": "plain_text", "text": text},
            },
            {
                "type": "actions",
                "block_id": "fleet_daily_action",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "운영 현황 확인"},
                        "url": url,
                    },
                ],
            },
        ),
        url,
    )
    return NotificationIntent(
        dedupe_key=f"FLEET_HEARTBEAT:{local_day}",
        notification_type="FLEET_HEARTBEAT",
        message=message,
    )

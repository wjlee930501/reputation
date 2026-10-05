"""Zero-provider daily fleet summary, separate from intervention alerts."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import String, and_, case, cast, column, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import aliased

from app.models.content import ContentItem
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
from app.services.notification_labels import prefixed_for_event
from app.services.notification_milestone_rendering import safe_text
from app.services.operator_action import requires_operator_action
from app.services.pipeline_watchdog import KST, WatchdogReport
from app.services.post_publish_review_policy import publicly_operational_hospital_predicate
from app.utils.db_locks import _is_postgres_bind


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


_FAILED_STATES = ("FAILED", "PARTIAL")
# 10/4 분석이 부풀린 몫으로 짚은 세 계열만 계보 규칙을 더한다. 다른 실행 종류는 종전 규칙
# (사고 링크·명시적 재시도 계보)만으로 판정한다.
_GENERATION_ATTEMPT_TYPES = ("GENERATE_CONTENT_ITEM", "REGENERATE_CONTENT", "REGENERATE_CONTENT_IMAGE")
# 글 하나를 처음부터 끝까지 만든 실행. 이미지 교체 성공은 본문이 생겼다는 근거가 아니다.
_FULL_GENERATION_TYPES = ("GENERATE_CONTENT_ITEM", "REGENERATE_CONTENT")
_GENERATION_BATCH_TYPE = "NIGHTLY_CONTENT_GENERATION"
_MONTHLY_BATCH_TYPE = "MONTHLY_REPORT_BATCH"
_MONTHLY_REPORT_TYPES = ("SCHEDULED_MONTHLY_REPORT", "GENERATE_MONTHLY_REPORT")
_UUID_TEXT = "^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$"
_PERIOD_TEXT = "^[0-9]{4}-[0-9]{2}$"


def _content_target(run):
    # 실행이 맡은 글(`operation_run_payloads.build_request_payload`의 `_dispatch.target_id`).
    return run.request_payload["_dispatch"]["target_id"].as_string()


def _uuid_or_null(text):
    # 깨진 값이 cast 오류로 요약 전체를 실패시키지 않게 하고, 맞는 값은 PK 인덱스를 쓴다.
    return case((text.regexp_match(_UUID_TEXT), cast(text, PG_UUID(as_uuid=True))), else_=None)


def _automatic_retry_in_progress(item_key, hospital_id, now):
    """재시도 정책이 그 글을 아직 소유한다 — 기한이 남은 RETRYING 생성 사고가 그 근거다.

    시도 예산·스윕 창 판정(`generation_incident_control.scheduled_recovery_owns_blocker`)은
    생성 사고의 RETRYING과 기한으로 이미 투영된다. SQL로 예산을 다시 계산하지 않고 그 투영과
    운영센터의 사람의 일 규칙(`requires_operator_action`)을 그대로 쓴다. 기한이 없는 RETRYING은
    끝이 정해지지 않았으므로 '진행 중'으로 치지 않고, 같은 글에 사람의 일(OPEN·기한 지난
    RETRYING)이 하나라도 있으면 진행 중이 아니다.
    """

    same_item = (
        Incident.hospital_id == hospital_id,
        Incident.source_type == "CONTENT_GENERATION",
        Incident.source_id == item_key,
    )
    owned = exists(
        select(Incident.id)
        .where(
            *same_item,
            Incident.state == IncidentState.RETRYING.value,
            Incident.sla_due_at >= now,
        )
        .correlate_except(Incident)
    )
    operator_work = exists(
        select(Incident.id)
        .where(
            *same_item,
            or_(
                Incident.state == IncidentState.OPEN.value,
                and_(Incident.state == IncidentState.RETRYING.value, Incident.sla_due_at < now),
            ),
        )
        .correlate_except(Incident)
    )
    return and_(owned, ~operator_work)


def _content_item_recovered(item_key, hospital_id, failed_at, now, later_type):
    """그 글에 이 실패가 더는 필요 없다는 구체적 근거(`task_incident_control._resolved_run_condition`).

    같은 글의 더 나중 성공, 실패 뒤의 최초 공개, 기한 안의 자동 재시도 중 하나다. 병원을 함께
    맞춰 다른 병원의 행을 근거로 쓰지 않고, 큰 `operation_runs`도 병원 인덱스로 좁힌다.
    배치 안에서는 두 단계 아래에서 바깥 실행을 참조하므로 상관을 명시한다 — 자동 상관은 바로
    위 단계만 보며, 놓치면 바깥 `operation_runs`가 교차 조인으로 다시 들어온다.
    """

    later = aliased(OperationRun)
    later_success = exists(
        select(later.id)
        .where(
            later.hospital_id == hospital_id,
            later_type(later),
            later.state == "SUCCEEDED",
            _content_target(later) == item_key,
            later.requested_at > failed_at,
        )
        .correlate_except(later)
    )
    published_after = exists(
        select(ContentItem.id)
        .where(
            ContentItem.id == _uuid_or_null(item_key),
            ContentItem.hospital_id == hospital_id,
            ContentItem.first_published_at > failed_at,
        )
        .correlate_except(ContentItem)
    )
    return or_(later_success, published_after, _automatic_retry_in_progress(item_key, hospital_id, now))


def _batch_items(run):
    items = run.result_summary["items"]
    safe = case((func.jsonb_typeof(items) == "object", items), else_=literal({}, JSONB))
    return func.jsonb_each(safe).table_valued(column("key", String), column("value", JSONB))


def _report_period(run):
    """월간 실행의 보고 기간(YYYY-MM). `tasks._latest_monthly_report_operation_run`과 같은 순서다."""

    year = run.result_summary["period_year"].as_string()
    month = run.result_summary["period_month"].as_string()
    source = run.request_payload["source_id"].as_string()
    return func.coalesce(
        case(
            (
                and_(year.regexp_match("^[0-9]{1,4}$"), month.regexp_match("^[0-9]{1,2}$")),
                func.concat(func.lpad(year, 4, "0"), "-", func.lpad(month, 2, "0")),
            ),
            else_=None,
        ),
        case((source.regexp_match(_PERIOD_TEXT), source), else_=None),
        func.substring(
            run.idempotency_key, "^(?:scheduled|coverage-recovery):[^:]+:([0-9]{4}-[0-9]{2})$"
        ),
    )


def _recovered_by_same_target(now):
    """자동화가 남기지 않는 재시도 계보 대신, 같은 대상의 구체적 근거로 실패를 설명한다.

    모든 항은 NULL이 되지 않는 판정(`IN`·`EXISTS`·`IS NOT NULL`)만 쓴다 — NULL이 섞이면 `NOT`이
    거짓이 돼 근거 없는 실패가 조용히 빠진다.
    """

    run = OperationRun
    # 글 단위 시도(GENERATE_CONTENT_ITEM·REGENERATE_CONTENT(_IMAGE)): 다음 스윕의 재시도는 새 배치
    # 아래 새 실행이라 parent 계보가 없다. 같은 종류·같은 글의 더 나중 성공이 근거다.
    item_attempt = and_(
        run.operation_type.in_(_GENERATION_ATTEMPT_TYPES),
        _content_item_recovered(
            _content_target(run),
            run.hospital_id,
            run.requested_at,
            now,
            lambda later: later.operation_type == run.operation_type,
        ),
    )
    # 23:00·스윕 배치: 실패한 글은 `result_summary.items`에만 남는다. 실패한 글이 하나 이상
    # 기록돼 있고 그 하나하나가 설명될 때만 뺀다. 설명 안 된 글이 남으면 배치를 센다 — 그 글의
    # 시도 실행이 따로 있으면 그것도 자기 규칙대로 센다(배치=배포 결과, 자식=그 시도). 둘을
    # 합쳐 하나로 세는 규칙은 두지 않는다. 덜 세는 쪽으로 틀리지 않게 하려는 선택이다.
    # 배치 행은 같은 실행이 다시 돌며 갱신되므로 글의 실패 시각은 배치의 마지막 종료로 본다.
    batch_failed_at = func.coalesce(run.completed_at, run.updated_at)

    def failed_entries():
        entry = _batch_items(run).alias("batch_item")
        return entry, entry.c.value["state"].astext.in_(_FAILED_STATES)

    entry, failed = failed_entries()
    has_failed_item = exists(select(literal(1)).select_from(entry).where(failed))
    entry, failed = failed_entries()
    item = aliased(ContentItem)
    unexplained_item = exists(
        select(literal(1))
        .select_from(entry)
        .outerjoin(item, item.id == _uuid_or_null(entry.c.key))
        .where(
            failed,
            ~_content_item_recovered(
                entry.c.key,
                item.hospital_id,
                batch_failed_at,
                now,
                lambda later: later.operation_type.in_(_FULL_GENERATION_TYPES),
            ),
        )
    )
    generation_batch = and_(
        run.operation_type == _GENERATION_BATCH_TYPE, has_failed_item, ~unexplained_item
    )
    # 월간 배치: 결과에 실패 병원 목록이 남지 않는다. 같은 기간의 더 나중 배치가 SUCCEEDED면
    # 그 기간의 모든 대상 병원이 끝났다는 뜻이다(`generate_monthly_reports`). PARTIAL은 아니다.
    later_batch = aliased(OperationRun)
    monthly_batch = and_(
        run.operation_type == _MONTHLY_BATCH_TYPE,
        exists(
            select(later_batch.id).where(
                later_batch.operation_type == _MONTHLY_BATCH_TYPE,
                later_batch.state == "SUCCEEDED",
                later_batch.request_payload["source_id"].as_string()
                == run.request_payload["source_id"].as_string(),
                later_batch.requested_at > run.requested_at,
            )
        ),
    )
    # 병원별 월간 리포트: 같은 병원·같은 기간의 더 나중 성공(정기 마감이든 커버리지 복구든).
    later_report = aliased(OperationRun)
    period = _report_period(run)
    monthly_report = and_(
        run.operation_type.in_(_MONTHLY_REPORT_TYPES),
        run.hospital_id.is_not(None),
        period.is_not(None),
        exists(
            select(later_report.id).where(
                later_report.hospital_id == run.hospital_id,
                later_report.operation_type.in_(_MONTHLY_REPORT_TYPES),
                later_report.state == "SUCCEEDED",
                later_report.requested_at > run.requested_at,
                _report_period(later_report) == period,
            )
        ),
    )
    return or_(item_attempt, generation_batch, monthly_batch, monthly_report)


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
    # lineage or concrete same-target evidence (`_recovered_by_same_target`).
    # Another month's report, another article, another hospital or an earlier
    # success is not recovery proof.
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
    unresolved_filters = [
        OperationRun.state.in_(["FAILED", "PARTIAL"]),
        OperationRun.updated_at >= now - timedelta(days=1),
        ~accounted_incident,
        ~later_success,
    ]
    # 같은 대상 판정은 Postgres JSONB·정규식을 쓴다. 운영 DB는 항상 Postgres이고, 다른 바인딩
    # (단위 테스트의 SQLite)에서는 종전 규칙만 적용해 덜 세는 쪽으로 틀리지 않는다.
    if _is_postgres_bind(db):
        unresolved_filters.append(~_recovered_by_same_target(now))
    unresolved = int(db.scalar(
        select(func.count()).select_from(OperationRun).where(*unresolved_filters)
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
    title = prefixed_for_event("FLEET_HEARTBEAT", f"[일일 요약] {local_day} · {state}")
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

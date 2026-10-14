"""이미 해결된 일을 가리키는 열린 사고를 DB 근거로 닫는다(2026-10-02).

사고는 대개 실패한 그 대상·그 주·그 기간에 묶여, 다음 주 측정이나 다음 기간이 정상으로
지나가도 아무도 닫지 않았다. 일일 운영 요약이 8월 사고까지 '확인 필요'로 세며 쌓였다.

종류마다 '해결됐다'는 근거 하나를 정의한다. 근거가 없으면 그대로 둔다 — 아직 진행 중인
문제(예: 측정 기준 불일치로 계속 막힌 주간 측정, 검증이 맞지 않는 원장 PDF)는 열려 있어야
한다. 자동 재시도 중(RETRYING)인 사고는 자동화의 것이라 건드리지 않는다. 단 스윕 소유 종류
(`incident_types.SWEEP_OWNED_INCIDENT_TYPES`)는 처음부터 RETRYING으로 열리므로 같은 근거로 닫는다.
닫는 근거가 없는 종류를 새로 만들지 않는다 — 새 종류는 여기 resolver 또는 명시적 복구 경로를 함께 둔다.

Slack 짝 계약: 그 사고의 '조치 필요'(INCIDENT_OPEN)가 outbox에 들어간 적이 있으면 닫을 때 '해결'
(INCIDENT_RECOVERED)을 함께 넣는다. 그 열림이 채널에 닿지 않았다면 발송기가 둘을 함께 종결한다
(`notification_store.settle_incident_notices`). 열림 알림이 없던 사고는 감사만 남기고 조용히 닫는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    DeliveryStatus,
    LeadDelivery,
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
from app.services.dependency_incident_helpers import open_notice_exists_sync
from app.services.finding_criticality import flag_findings_are_critical
from app.services.incident_types import DEFAULT_RETRYING_DEADLINE, SWEEP_OWNED_INCIDENT_TYPES
from app.services.monthly_period import monthly_final_judgment_at
from app.services.monthly_report_readiness import load_period_report_state
from app.services.notification_messages import build_recovered_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.services.notification_transport import CHANNEL_UNAVAILABLE_CODE, safe_error_message
from app.services.published_correction import POST_PUBLISH_FLAG_KEY
from app.workers.task_incident_control import _audit, _projection, _transition_incident

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
    if succeeded is not None:
        return "same_period_measurement_succeeded"
    if incident.incident_type == "MONTHLY_SOV_MEASUREMENT_FAILED":
        return _monthly_period_finalized(db, incident, _now)
    return None


def _monthly_period_finalized(db: Session, incident: Incident, now: datetime) -> str | None:
    """최종 판정 시각(다음 달 8일 09:00 KST)이 지났고 그 달 월간 보고서가 있으면 측정 단계는 끝났다.

    마감으로 LIMITED/UNAVAILABLE이 된 달에는 새 RUN_SOV 성공이 없어, 같은 기간 측정 성공만 보던
    근거로는 사고가 영원히 열려 있었다(2026-09 신기한·장앤김, 7.6일). 측정 결손은 보고서 품질이
    말하고, 보고서의 전달 가능성은 월간 보고서 안전망(`monthly_report_safety_net`)이 소유한다.
    """
    period = _measurement_period(incident)
    try:
        year_text, month_text = str(period or "").split("-", 1)
        year, month = int(year_text), int(month_text)
    except ValueError:
        return None
    if not 1 <= month <= 12 or now < monthly_final_judgment_at(year, month):
        return None
    quality = db.scalar(
        select(MonthlyReport.quality)
        .where(
            MonthlyReport.hospital_id == incident.hospital_id,
            MonthlyReport.period_year == year,
            MonthlyReport.period_month == month,
            MonthlyReport.report_type == "MONTHLY",
        )
        .order_by(MonthlyReport.version.desc())
        .limit(1)
    )
    return f"finalized_as_{str(quality).lower()}" if quality else None


def _period_report_deliverable(db: Session, incident: Incident, _now: datetime) -> str | None:
    """월간 보고서 안전망 사고 — 그 병원·월의 최신 월간 보고서가 전달 게이트를 통과하면 닫는다."""
    if incident.hospital_id is None:
        return None
    period = _measurement_period(incident)
    try:
        year_text, month_text = str(period or "").split("-", 1)
        year, month = int(year_text), int(month_text)
    except ValueError:
        return None
    state = load_period_report_state(db, incident.hospital_id, year, month)
    return "monthly_report_deliverable" if state.gate.ready else None


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
    db: Session, incident: Incident, source_types: tuple[str, ...] = ("LEAD_DIAGNOSIS",)
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
    # 리포트 차단 사고(`LEAD_REPORT_BLOCKED`)는 같은 진단을 `LEAD_REPORT` 출처로 가리킨다.
    if incident.source_type not in source_types:
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


def _lead_report_recovered(
    db: Session,
    incident: Incident,
    _now: datetime,
    *,
    source_types: tuple[str, ...] = ("LEAD_DIAGNOSIS",),
) -> str | None:
    diagnosis, gone = _lead_diagnosis_gone(db, incident, source_types)
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


def _lead_followed_up(db: Session, incident: Incident, _now: datetime) -> str | None:
    """리드 부수효과(접수 알림·안내 문자) 실패는 사람이 그 리드를 맡으면(상태 ≠ NEW) 끝난다."""
    try:
        lead_id = uuid.UUID(str(incident.source_id))
    except (TypeError, ValueError):
        return None
    lead = db.get(SalesLead, lead_id)
    if lead is None or lead.purged_at is not None:
        return "lead_purged"
    return "lead_followed_up" if (lead.status or "NEW") != "NEW" else None


def _uuid_source(incident: Incident) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(incident.source_id))
    except ValueError:
        return None


def _lead_delivery_settled(db: Session, incident: Incident, _now: datetime) -> str | None:
    """중단된 진단 메일이 더 이상 보낼 것이 없다.

    수동 재발송은 같은 행을 다시 쓴다(`lead_delivery.send_report_email`, 진단·이벤트·채널 유일).
    그래서 근거는 그 행 하나다: 행이 없다(리드 파기 CASCADE), 보냈다(SENT), 고객 채널이 아니다(INTERNAL).
    """

    delivery_id = _uuid_source(incident)
    if delivery_id is None:
        return None
    status = db.scalar(select(LeadDelivery.status).where(LeadDelivery.id == delivery_id))
    if status is None:
        return "lead_delivery_missing"
    if status == DeliveryStatus.SENT.value:
        return "lead_delivery_sent"
    if status == DeliveryStatus.INTERNAL.value:
        return "lead_delivery_internal_only"
    return None


def _must_use_version_replaced(db: Session, incident: Incident, _now: datetime) -> str | None:
    """제외 문구가 든 승인본이 더 이상 현재 승인본이 아니다(병원당 APPROVED 하나)."""

    philosophy_id = _uuid_source(incident)
    if philosophy_id is None:
        return None
    status = db.scalar(
        select(HospitalContentPhilosophy.status).where(
            HospitalContentPhilosophy.id == philosophy_id
        )
    )
    if status is None:
        return "approved_version_missing"
    return None if status == PhilosophyStatus.APPROVED else "approved_version_replaced"


def _naver_post_stored(db: Session, incident: Incident, _now: datetime) -> str | None:
    """그 병원의 더 나중 수집 실행이 같은 글을 저장했거나 이미 저장된 중복으로 건너뛰었다."""

    if incident.hospital_id is None or not incident.source_id:
        return None
    items = cast(OperationRun.result_summary, JSONB)["items"]
    url_hash = str(incident.source_id)
    stored = db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == incident.hospital_id,
            OperationRun.request_payload["source_type"].as_string() == "NAVER_BLOG",
            OperationRun.requested_at > incident.first_seen_at,
            or_(
                items.contains([{"url_hash": url_hash, "state": "INGESTED"}]),
                items.contains([{"url_hash": url_hash, "state": "SKIPPED"}]),
            ),
        ).limit(1)
    )
    return "naver_post_stored" if stored is not None else None


def _broker_accepted_later(db: Session, incident: Incident, _now: datetime) -> str | None:
    """같은 종류·같은 병원의 실행이 마지막 실패 관측 뒤 큐에 들어갔다(브로커가 받았다)."""

    failed = db.get(OperationRun, incident.operation_run_id) if incident.operation_run_id else None
    if failed is None:
        return None
    accepted = db.scalar(
        select(OperationRun.id).where(
            OperationRun.operation_type == failed.operation_type,
            OperationRun.hospital_id.is_not_distinct_from(incident.hospital_id),
            OperationRun.queued_at > incident.last_seen_at,
        ).limit(1)
    )
    return "broker_accepted_later_dispatch" if accepted is not None else None


def _site_build_dispatch_superseded(
    db: Session, incident: Incident, _now: datetime
) -> str | None:
    if incident.hospital_id is None:
        return None
    hospital = db.get(Hospital, incident.hospital_id)
    if hospital is None:
        return "hospital_missing"
    if hospital.site_built:
        return "site_built"
    # 1분 자율 복구가 사이트 준비를 맡았다. 그 예산 소진은 SITE_BUILD_RETRIES_EXHAUSTED가 따로 연다.
    swept = db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == incident.hospital_id,
            OperationRun.operation_type == "REBUILD_SITE",
            OperationRun.requested_at > incident.first_seen_at,
        ).limit(1)
    )
    return "site_build_sweep_owns_retry" if swept is not None else None


def _content_dispatch_superseded(db: Session, incident: Incident, now: datetime) -> str | None:
    """오늘·내일 슬롯의 즉시 생성 투입 실패. 그 이틀 글이 다 써졌거나, 하루가 지나면 닫는다."""

    if incident.hospital_id is None:
        return None
    first_day = incident.first_seen_at.astimezone(_KST).date()
    unwritten = db.scalar(
        select(ContentItem.id).where(
            ContentItem.hospital_id == incident.hospital_id,
            ContentItem.scheduled_date.between(first_day, first_day + timedelta(days=1)),
            ContentItem.status.in_((ContentStatus.DRAFT, ContentStatus.READY)),
            or_(ContentItem.body.is_(None), func.btrim(ContentItem.body) == ""),
        ).limit(1)
    )
    if unwritten is None:
        return "near_slots_generated"
    # 스윕 소유 기한(사고에 저장된 `sla_due_at`)이 지나면 남은 글은 야간·복구 스윕과 글 단위
    # 생성 사고(CONTENT_GENERATION_FAILED)가 소유한다. 기한 없는 옛 OPEN 행은 관측 + 하루로 본다.
    handover = incident.sla_due_at or incident.first_seen_at + DEFAULT_RETRYING_DEADLINE
    if handover <= now:
        return "per_item_generation_owns_retry"
    return None


def _month_closed(_db: Session, incident: Incident, now: datetime) -> str | None:
    """`source_id="YYYY-MM"`인 사고 — 그 달(KST)이 끝나면 손쓸 것이 없다."""

    try:
        first = datetime.strptime(str(incident.source_id or ""), "%Y-%m").date()
    except ValueError:
        return None
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return "cohort_month_ended" if now.astimezone(_KST).date() > last else None


_UNVERIFIED_ACTOR_QUIET_PERIOD = timedelta(days=30)


def _unverified_actor_quiet(_db: Session, incident: Incident, now: datetime) -> str | None:
    if incident.last_seen_at <= now - _UNVERIFIED_ACTOR_QUIET_PERIOD:
        return "no_repeat_for_30_days"
    return None


# 비용 가드 fail-open 사고는 알린 프로세스가 Redis로 다시 판정할 때 닫는다(`cost_guard_health`).
# 그 프로세스가 복구 전에 교체되면 메모리 상태가 사라져 아무도 닫지 않는다 — 여기서 닫는다. 근거는
# PING이 아니라 가드가 실제로 하는 예산 연산의 성공이다(`cost_guard.probe_budget_operation`): Redis가
# 응답해도 예산 Lua는 OOM·권한·키 타입 오류로 계속 실패할 수 있다. 그리고 마지막 관측 뒤 10분은
# 기다린다 — 실패하는 프로세스는 5분마다 관측을 다시 기록하므로(`cost_guard_health._TOUCH_SECONDS`)
# 실패가 이어지는 동안 이 창은 열리지 않는다.
_COST_GUARD_FAIL_OPEN_QUIET = timedelta(minutes=10)


def _cost_guard_category(incident: Incident) -> str | None:
    """사고의 `source_id`(`{category}:fail_open`)에서 비용 가드 카테고리를 읽는다."""

    from app.services.cost_guard import CATEGORIES

    category = str(incident.source_id or "").split(":", 1)[0]
    return category if category in CATEGORIES else None


def _cost_guard_budget_op_succeeds(category: str) -> bool:
    from app.services.cost_guard import probe_budget_operation

    return probe_budget_operation(category)


def _cost_guard_judging_again(_db: Session, incident: Incident, now: datetime) -> str | None:
    if incident.last_seen_at is not None and now - incident.last_seen_at < _COST_GUARD_FAIL_OPEN_QUIET:
        return None
    category = _cost_guard_category(incident)
    if category is None:
        return None
    return "cost_guard_budget_op_succeeded" if _cost_guard_budget_op_succeeds(category) else None


# 자율 복구(`autonomous_recovery._stop_operation_run`)가 실행을 멈추며 남기는 원인 코드.
# autonomous_recovery가 이 모듈을 불러 쓰므로 여기에 문자열로 두고, 테스트가 두 목록을 맞춘다.
RECOVERY_STOP_CODES = frozenset(
    {"OPERATION_REDISPATCH_EXHAUSTED", "OPERATION_LEASE_EXPIRED", "OPERATION_RUN_ABANDONED"}
)


def _has_succeeded_follow_up(db: Session, run) -> bool:
    """멈춘 실행 뒤에 같은 일이 성공했는가.

    운영 관제의 '작업 다시 시도'(`operation_runs.retry_operation_run`)는 `parent_run_id`로 실패
    실행을 가리키는 같은 종류의 새 실행을 만들고, 그 재시도가 또 실패하면 다시 그 아래로 잇는다.
    같은 종류로 이어진 후손 중 하나라도 SUCCEEDED면 근거다. 종류를 묶는 이유: 야간 배치의 자식
    (글 단위 생성)도 `parent_run_id`로 배치를 가리키지만 배치의 재시도가 아니다.

    병원 없는 실행(야간 배치 잔재 등)은 운영 관제가 다시 시도할 수 없다(병원 범위 조회). 그런
    실행은 같은 종류의 병원 없는 실행이 그 뒤에 성공한 것이 근거다 — 다음 밤 배치가 같은 일을 했다.
    """

    child = aliased(OperationRun)
    retries = (
        select(OperationRun.id)
        .where(
            OperationRun.parent_run_id == run.id,
            OperationRun.operation_type == run.operation_type,
        )
        .cte("recovery_stop_retries", recursive=True)
    )
    retries = retries.union_all(
        select(child.id).where(
            child.parent_run_id == retries.c.id,
            child.operation_type == run.operation_type,
        )
    )
    succeeded = db.scalar(
        select(OperationRun.id)
        .where(
            OperationRun.id.in_(select(retries.c.id)),
            OperationRun.state == OperationRunState.SUCCEEDED,
        )
        .limit(1)
    )
    if succeeded is not None:
        return True
    if run.hospital_id is not None:
        return False
    later = db.scalar(
        select(OperationRun.id)
        .where(
            OperationRun.operation_type == run.operation_type,
            OperationRun.hospital_id.is_(None),
            OperationRun.state == OperationRunState.SUCCEEDED,
            OperationRun.completed_at > run.completed_at,
        )
        .limit(1)
    )
    return later is not None


def _stopped_runs_recovered(db: Session, incident: Incident, _now: datetime) -> str | None:
    """이 원인으로 멈춘 실행(이번 에피소드)이 모두 후속 성공을 가지면 닫는다(D5)."""

    code = incident.safe_error_code
    if code not in RECOVERY_STOP_CODES:
        return None
    stopped = db.execute(
        select(
            OperationRun.id,
            OperationRun.operation_type,
            OperationRun.hospital_id,
            OperationRun.completed_at,
        ).where(
            OperationRun.state == OperationRunState.FAILED,
            OperationRun.safe_error_code == code,
            # 다시 열린 사고의 first_seen_at은 새 에피소드의 시작이다 — 앞 에피소드는 이미 닫혔다.
            OperationRun.completed_at >= incident.first_seen_at,
        )
    ).all()
    if not stopped:
        return None
    if all(_has_succeeded_follow_up(db, run) for run in stopped):
        return "stopped_runs_recovered"
    return None


def _post_publish_flag_settled(db: Session, incident: Incident, _now: datetime) -> str | None:
    """공개 글 사후 검수 사고의 근거(D35): 글이 내려갔거나, 확인됐거나, 표시가 풀렸거나, Critical이 아니다.

    Critical 지적이 그대로인 공개 글의 사고는 사람이 확인할 때까지 남는다. 옛 조용한 사고
    (`POST_PUBLISH_REVIEW_FLAGGED`)도 같은 근거로 닫힌다 — Critical이 아닌 지적은 사고가 아니다.
    """

    item = db.get(ContentItem, incident.source_id) if incident.source_id else None
    if item is None or item.status != ContentStatus.PUBLISHED:
        return "content_not_public"
    if item.post_publish_reviewed_at is not None:
        return "post_publish_reviewed"
    summary = item.essence_check_summary if isinstance(item.essence_check_summary, dict) else {}
    marker = summary.get(POST_PUBLISH_FLAG_KEY)
    if not isinstance(marker, dict) or marker.get("status") != "FLAGGED":
        return "flag_cleared"
    if not flag_findings_are_critical(marker.get("structured_findings"), item):
        return "flag_not_critical"
    return None


RESOLVERS: dict[str, Resolver] = {
    "OPERATION_RECOVERY_EXHAUSTED": _stopped_runs_recovered,
    "MONTHLY_SOV_COHORT_GAP": _monthly_cohort_enrolled,
    "WEEKLY_SOV_MEASUREMENT_FAILED": _same_period_measurement,
    "MONTHLY_SOV_MEASUREMENT_FAILED": _same_period_measurement,
    "MONTHLY_REPORT_SAFETY_NET_FAILED": _period_report_deliverable,
    "SOV_HIGH_PRIORITY_CAP_EXCEEDED": _weekly_capacity,
    "COST_GUARD_LIMIT_REACHED": _budget_period,
    "COST_GUARD_FAIL_OPEN": _cost_guard_judging_again,
    "V0_REPORT_FAILED": _v0_report_created,
    "CONTENT_GENERATION_FAILED": _content_published,
    "NOTIFICATION_DELIVERY_UNKNOWN": _redirected_delivery,
    "RECOVER_LEAD_MEASUREMENT": _lead_measurement_recovered,
    "RECOVER_LEAD_REPORT": _lead_report_recovered,
    # Phase 1-2: 닫는 코드가 없던 종류
    "LEAD_DIAGNOSIS_FAILED": _lead_measurement_recovered,
    "LEAD_DIAGNOSIS_RETRIES_EXHAUSTED": _lead_measurement_recovered,
    "LEAD_REPORT_BLOCKED": partial(_lead_report_recovered, source_types=("LEAD_REPORT",)),
    "LEAD_DELIVERY_ABANDONED": _lead_delivery_settled,
    "ESSENCE_MUST_USE_EXCLUDED": _must_use_version_replaced,
    "NAVER_SOURCE_FETCH_FAILED": _naver_post_stored,
    "BROKER_UNAVAILABLE": _broker_accepted_later,
    "SITE_BUILD_DISPATCH_FAILED": _site_build_dispatch_superseded,
    "CONTENT_DISPATCH_FAILED": _content_dispatch_superseded,
    "MONTHLY_SOV_COHORT_OVER_LIMIT": _month_closed,
    "UNVERIFIED_ADMIN_ACTOR": _unverified_actor_quiet,
    # Phase 1-9c: 도입문의 부수효과 실패 — 리드를 맡으면 닫힌다.
    "LEAD_INTAKE_NOTIFICATION_FAILED": _lead_followed_up,
    "INQUIRY_SMS_FAILED": _lead_followed_up,
    # D35: 공개 글 사후 검수 — Critical 사고와 옛 조용한 지적 사고.
    "POST_PUBLISH_CRITICAL_FINDING": _post_publish_flag_settled,
    "POST_PUBLISH_REVIEW_FLAGGED": _post_publish_flag_settled,
}
# 스윕 소유 종류는 RETRYING으로 열리므로 그 상태에서도 근거로 닫는다. 나머지 RETRYING은 자동화의 것이다.
_RETRYING_RESOLVABLE = tuple(sorted(SWEEP_OWNED_INCIDENT_TYPES & set(RESOLVERS)))


def _finish_recovered(
    db: Session, recovered: Incident, action: str, **detail: str | bool
) -> Incident:
    """RECOVERED가 된 사고를 시스템 확인으로 닫는다. 나간 열림 알림이 있으면 복구 알림을 넣는다.

    "채널에 나간 '조치 필요'는 반드시 '해결'로 닫는다"(`open_notice_exists_sync`)를 근거·연결 실행
    성공 어느 쪽으로 닫든 같은 규칙으로 지킨다. 감사의 `slack_suppressed`는 복구 알림을 넣지 않았는가다.
    """

    notified = open_notice_exists_sync(db, recovered.id)
    if notified:
        enqueue_notification_sync(
            db,
            build_recovered_incident_notification(
                _projection(db, recovered), settings.ADMIN_BASE_URL
            ),
        )
    acknowledged = _transition_incident(
        db,
        recovered,
        expected_state=IncidentState.RECOVERED,
        next_state=IncidentState.ACKNOWLEDGED,
        acknowledged=True,
    )
    closed = acknowledged or recovered
    _audit(db, closed, action, detail_extra={**detail, "slack_suppressed": not notified})
    return closed


def close_resolved_backlog_incidents(
    db: Session, *, limit: int = BACKLOG_INCIDENT_BATCH, now: datetime | None = None
) -> int:
    """근거가 있는 열린 사고를 닫는다. 나간 열림 알림이 있을 때만 복구 알림을 넣는다. 커밋은 호출자."""

    observed = now or datetime.now(UTC)
    candidates = list(
        db.execute(
            select(Incident)
            .where(
                or_(
                    Incident.state == IncidentState.OPEN.value,
                    and_(
                        Incident.state == IncidentState.RETRYING.value,
                        Incident.incident_type.in_(_RETRYING_RESOLVABLE),
                    ),
                ),
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
        retrying: Incident | None = incident
        if incident.state == IncidentState.OPEN.value:
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
        _finish_recovered(db, recovered, "incident_resolved_by_later_evidence", evidence=reason)
        closed += 1
    return closed


def default_retry_deadlines(
    db: Session, *, now: datetime, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """기한 없이 남은 RETRYING에 마지막 변경 + 24시간 기한을 단다(배포 전 행·옛 호출자).

    기한 없는 RETRYING은 `requires_operator_action`이 영원히 사람의 일로 보지 않는 '주인 없는
    복구 중'이다. MISSING_APPROVED_ESSENCE 생성 보류는 사람의 일을 별도 ESCALATED 사고가
    가지므로 제외한다(같은 원인을 두 번 세지 않는다).
    """

    rows = list(
        db.execute(
            select(Incident)
            .where(
                Incident.state == IncidentState.RETRYING.value,
                Incident.sla_due_at.is_(None),
                or_(
                    Incident.incident_type != "CONTENT_GENERATION_FAILED",
                    Incident.safe_error_code.is_distinct_from("MISSING_APPROVED_ESSENCE"),
                ),
            )
            .order_by(Incident.updated_at, Incident.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for incident in rows:
        incident.sla_due_at = incident.updated_at + DEFAULT_RETRYING_DEADLINE
        incident.version += 1
        incident.updated_at = now
        _audit(
            db,
            incident,
            "incident_retry_deadline_defaulted",
            detail_extra={"sla_due_at": incident.sla_due_at.isoformat()},
        )
    if rows:
        db.flush()
    return len(rows)


# 생성 사고는 연결 실행이 끝나도 글이 막혀 있을 수 있고 자체 복구(`generation_incident_control`)가 닫는다.
_OWNER_MANAGED_SOURCES = ("CONTENT_GENERATION",)
_RECOVERED_ACK_GRACE = timedelta(hours=24)


def close_retrying_with_linked_success(
    db: Session, *, now: datetime, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """Admin '복구 확인 완료'와 같은 근거(연결 실행 SUCCEEDED·같은 병원 범위)를 기계가 확인해 닫는다.

    버튼보다 좁다: 그 성공이 사고의 마지막 관측 뒤에 끝났을 때만이고, 생성 사고는 제외한다.
    짝 계약: 그 사고의 열림 알림이 outbox에 있었다면 복구 알림을 넣는다. 그 열림이 나가지
    않았다면 발송기가 복구 알림도 함께 종결한다(`notification_store.settle_incident_notices`).
    """

    rows = db.execute(
        select(Incident, OperationRun)
        .join(OperationRun, OperationRun.id == Incident.operation_run_id)
        .where(
            Incident.state == IncidentState.RETRYING.value,
            Incident.source_type.notin_(_OWNER_MANAGED_SOURCES),
            OperationRun.state == OperationRunState.SUCCEEDED.value,
            OperationRun.hospital_id.is_not_distinct_from(Incident.hospital_id),
            OperationRun.completed_at > Incident.last_seen_at,
        )
        .order_by(Incident.last_seen_at, Incident.id)
        .with_for_update(of=Incident, skip_locked=True)
        .limit(limit)
    ).all()
    closed = 0
    for incident, linked in rows:
        recovered = _transition_incident(
            db,
            incident,
            expected_state=IncidentState.RETRYING,
            next_state=IncidentState.RECOVERED,
            recovered=True,
        )
        if recovered is None:
            continue
        _finish_recovered(
            db,
            recovered,
            "incident_closed_by_linked_success",
            evidence="linked_run_succeeded",
            linked_run_id=str(linked.id),
        )
        closed += 1
    return closed


def acknowledge_settled_recoveries(
    db: Session, *, now: datetime, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """복구된 지 하루가 지난 사고를 시스템 확인으로 닫는다(acknowledged_by_id NULL = 기계가 닫음).

    RECOVERED는 이미 사람의 일이 아니다(`operator_action.requires_operator_action`). '확인됨'
    화면의 클릭을 남기지 않으려는 정리이며 Slack을 만들지 않는다.
    """

    rows = list(
        db.execute(
            select(Incident)
            .where(
                Incident.state == IncidentState.RECOVERED.value,
                Incident.recovered_at <= now - _RECOVERED_ACK_GRACE,
            )
            .order_by(Incident.recovered_at, Incident.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    closed = 0
    for incident in rows:
        acknowledged = _transition_incident(
            db,
            incident,
            expected_state=IncidentState.RECOVERED,
            next_state=IncidentState.ACKNOWLEDGED,
            acknowledged=True,
        )
        if acknowledged is None:
            continue
        _audit(
            db,
            acknowledged,
            "incident_auto_acknowledged",
            detail_extra={"evidence": "recovered_over_24h"},
        )
        closed += 1
    return closed


def settle_incidents(
    db: Session, *, now: datetime | None = None, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """자동화가 끝낸 일을 사람의 일로 남기지 않는 정리. Slack은 짝 계약이 요구할 때만. 커밋은 호출자."""

    observed = now or datetime.now(UTC)
    return (
        default_retry_deadlines(db, now=observed, limit=limit)
        + close_retrying_with_linked_success(db, now=observed, limit=limit)
        + acknowledge_settled_recoveries(db, now=observed, limit=limit)
    )

"""Monthly readiness and delivery-correction milestone scan."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monthly_control import (
    MonthlyDeliveryEvent,
    ReportDeliveryEventType,
)
from app.services.monthly_events import (
    MonthlyEvent,
    MonthlyEventType,
    monthly_headline_label,
    project_monthly_event,
)
from app.services.monthly_period import KST
from app.services.monthly_report_delivery import coverage_is_final
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import MilestoneKind, MilestoneProjection
from app.workers.milestone_monthly_facts import ReportFacts, latest_report_facts, load_report_facts
from app.workers.milestone_projection_support import (
    MilestoneStateScan,
    ProjectionWindow,
    event_uuid,
    in_window,
    state_uuid,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _MonthlyEventRequest:
    facts: ReportFacts
    event_type: MonthlyEventType
    occurred_at: datetime
    event_id: uuid.UUID


async def scan_monthly_milestones(
    db: AsyncSession, window: ProjectionWindow
) -> tuple[MilestoneProjection, ...]:
    """Project report readiness and append-only delivery corrections in the window."""

    facts_by_report = await load_report_facts(db)
    projections = [
        current
        for facts in latest_report_facts(facts_by_report)
        if (current := _project_current(facts, window)) is not None
    ]
    projections.extend(
        await _project_delivery_events(db, facts_by_report, window.start, window.end)
    )
    return tuple(projections)


async def observe_monthly_milestones(
    db: AsyncSession,
    observed_at: datetime,
    previous_states: dict[str, str],
    delivery_since: datetime,
) -> MilestoneStateScan:
    """Return current report transitions plus unseen append-only delivery facts."""

    facts_by_report = await load_report_facts(db)
    observed: list[tuple[ReportFacts, tuple[str, MilestoneProjection]]] = []
    for facts in latest_report_facts(facts_by_report):
        if not _in_observation_scope(facts, observed_at):
            continue
        try:
            observed.append((facts, _project_observed_current(facts, observed_at)))
        except NotificationPayloadError as exc:
            # 한 리포트의 게이트 불일치가 다른 병원의 알림까지 멈추면 안 된다. 이 리포트만
            # 이번 창에서 건너뛰고 로그로 남긴다 — 다음 창에서 다시 시도한다.
            logger.warning("monthly milestone skipped: report=%s reason=%s", facts.report.id, exc)
    current = tuple(observed)
    states = {key: projection.stable_id for _facts, (key, projection) in current}
    changed = tuple(
        projection
        for facts, (key, projection) in current
        if previous_states.get(key) != projection.stable_id
        and not (
            facts.delivered and projection.kind is MilestoneKind.MONTHLY_CUSTOMER_READY
        )
    )
    deliveries = await _project_delivery_events(db, facts_by_report, delivery_since, observed_at)
    return MilestoneStateScan((*changed, *deliveries), states)


async def _project_delivery_events(
    db: AsyncSession,
    facts_by_report: dict[uuid.UUID, ReportFacts],
    since: datetime,
    until: datetime,
) -> tuple[MilestoneProjection, ...]:
    deliveries = (
        await db.execute(
            select(MonthlyDeliveryEvent).where(
                MonthlyDeliveryEvent.created_at >= since,
                MonthlyDeliveryEvent.created_at < until,
                MonthlyDeliveryEvent.event_type.in_(("CORRECTED", "RESCINDED", "REDELIVERED")),
            )
        )
    ).scalars()
    projections: list[MilestoneProjection] = []
    for delivery in deliveries:
        facts = facts_by_report.get(delivery.report_id)
        if facts is None:
            continue
        try:
            projections.append(
                project_monthly_event(
                    _monthly_event(
                        _MonthlyEventRequest(
                            facts,
                            _delivery_event_type(delivery.event_type),
                            delivery.created_at,
                            delivery.id,
                        )
                    )
                )
            )
        except NotificationPayloadError as exc:
            # 전달 기록 하나가 투영되지 않아도 나머지 병원의 기록은 그대로 나간다.
            logger.warning(
                "monthly delivery milestone skipped: delivery=%s reason=%s", delivery.id, exc
            )
    return tuple(projections)


def _project_current(facts: ReportFacts, window: ProjectionWindow) -> MilestoneProjection | None:
    event_type = _current_state(facts)
    if facts.delivered and event_type is MonthlyEventType.CUSTOMER_READY:
        return None
    occurred_at = _legacy_transition_time(facts, event_type)
    if occurred_at is None or not in_window(occurred_at, window):
        return None
    request = _MonthlyEventRequest(
        facts,
        event_type,
        occurred_at,
        event_uuid(event_type.value, facts.report.id, occurred_at),
    )
    return project_monthly_event(_monthly_event(request))


def _project_observed_current(
    facts: ReportFacts, observed_at: datetime
) -> tuple[str, MilestoneProjection]:
    event_type = _current_state(facts)
    request = _MonthlyEventRequest(
        facts,
        event_type,
        observed_at,
        state_uuid(event_type.value, facts.report.id, _state_fingerprint(facts, event_type)),
    )
    return f"monthly:{facts.report.id}", project_monthly_event(_monthly_event(request))


def _in_observation_scope(facts: ReportFacts, observed_at: datetime) -> bool:
    """Keep the current-state scan on months operators can still act on.

    이미 전달한 지난 계약 월의 리포트는 병원 공통 차단(예: 근거 자료 철회)이 켜지는
    순간 한 병원에서 여러 달치 차단이 한꺼번에 투영된다. 사람이 할 일은 그 병원의
    자료 하나이지 닫힌 달의 리포트가 아니다. 전달되지 않은 달은 지연 전달을 위해
    기간과 무관하게 남긴다 — 늦게 준비된 리포트의 전달 알림을 잃지 않는다.
    """

    if not facts.delivered:
        return True
    local = observed_at.astimezone(KST)
    report = facts.report
    return report.period_year * 12 + report.period_month >= local.year * 12 + local.month - 1


def _current_state(facts: ReportFacts) -> MonthlyEventType:
    report = facts.report
    if "CURRENT_READINESS_BLOCKED" in facts.blockers:
        return MonthlyEventType.BLOCKED
    if facts.ready and facts.artifact is not None:
        return MonthlyEventType.CUSTOMER_READY
    coverage_complete = (
        coverage_is_final(report)
        and facts.manifest is not None
        and facts.manifest.closed_at is not None
    )
    if coverage_complete and facts.artifact_state.value != "VALID":
        return MonthlyEventType.ARTIFACT_VALIDATION_PENDING
    return MonthlyEventType.BLOCKED


def _legacy_transition_time(facts: ReportFacts, event_type: MonthlyEventType) -> datetime | None:
    if event_type is MonthlyEventType.CUSTOMER_READY and facts.artifact is not None:
        return facts.artifact.validated_at or facts.artifact.created_at
    return facts.report.created_at


def _state_fingerprint(facts: ReportFacts, event_type: MonthlyEventType) -> str:
    """Fingerprint the notification a state would produce, not every underlying fact.

    차단이 이어지는 동안에도 표본 수·PDF 행·게이트 코드는 계속 움직인다. 그 움직임을
    상태 지문에 담으면 같은 차단이 관측 창마다 새 상태로 보여 Slack Error가 15분마다
    다시 나간다. BLOCKED는 운영자 문구와 조치 필요 여부를 가르는 값만 담아 차단이
    유지되는 동안 지문을 고정한다. 종류 자체는 `state_uuid`가 이미 묶으므로 다른
    상태로 넘어가는 전이는 그대로 새 알림이 된다. 차단 판정과 게이트는 건드리지 않는다.
    """

    if event_type is MonthlyEventType.BLOCKED:
        return ":".join(
            (
                str(facts.manifest is not None and facts.manifest.closed_at is not None),
                str("CURRENT_READINESS_BLOCKED" in facts.blockers),
            )
        )
    report = facts.report
    artifact_state = facts.artifact_state.value
    artifact_id = (
        "delivered"
        if facts.delivered
        else str(facts.artifact.id) if facts.artifact is not None else "none"
    )
    manifest_closed = facts.manifest is not None and facts.manifest.closed_at is not None
    return ":".join(
        (
            report.quality,
            str(report.planned_count),
            str(report.success_count),
            str(report.failed_count),
            str(manifest_closed),
            artifact_state,
            artifact_id,
            str(facts.delivered),
            *facts.blockers,
        )
    )


def _monthly_event(request: _MonthlyEventRequest) -> MonthlyEvent:
    facts = request.facts
    report = facts.report
    artifact = facts.artifact
    return MonthlyEvent(
        request.event_id,
        request.event_type,
        report.id,
        facts.hospital.id,
        facts.hospital.name,
        report.period_year,
        report.period_month,
        report.quality,
        report.planned_count,
        report.success_count,
        report.failed_count,
        coverage_is_final(report),
        facts.manifest is not None and facts.manifest.closed_at is not None,
        facts.artifact_state,
        artifact.id if artifact is not None else None,
        facts.ready,
        facts.blockers,
        "담당 AE",
        None,
        request.occurred_at,
        monthly_headline_label(report.sov_summary),
    )


def _delivery_event_type(value: str) -> MonthlyEventType:
    match ReportDeliveryEventType(value):
        case ReportDeliveryEventType.CORRECTED:
            return MonthlyEventType.DELIVERY_CORRECTED
        case ReportDeliveryEventType.RESCINDED:
            return MonthlyEventType.DELIVERY_RESCINDED
        case ReportDeliveryEventType.REDELIVERED:
            return MonthlyEventType.DELIVERY_REDELIVERED
        case ReportDeliveryEventType.DELIVERED:
            raise NotificationPayloadError("DELIVERED_EVENT_NOT_PROJECTED")
        case unreachable:
            assert_never(unreachable)

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
from app.services.notification_milestone_messages import MilestoneProjection
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
    redelivery_needed: bool = False


async def scan_monthly_milestones(
    db: AsyncSession, window: ProjectionWindow
) -> tuple[MilestoneProjection, ...]:
    """Project report readiness and append-only delivery corrections in the window."""

    facts_by_report = await load_report_facts(db)
    projections = [
        current
        for facts in latest_report_facts(facts_by_report)
        if (current := _project_current(facts, window, facts_by_report)) is not None
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
    delivered_months = _delivered_months(facts_by_report)
    legacy_months = _legacy_state_months(facts_by_report, previous_states)
    states: dict[str, str] = {}
    changed: list[MilestoneProjection] = []
    for facts in latest_report_facts(facts_by_report):
        if not _in_observation_scope(facts, observed_at):
            continue
        key = _month_key(facts)
        month = _month_of(facts)
        try:
            event_type = _current_state(facts)
            ever_delivered = month in delivered_months
            redelivery = (
                event_type is MonthlyEventType.CUSTOMER_READY
                and ever_delivered
                and not facts.delivered
                and _numbers_changed_since_prior(facts, facts_by_report)
            )
            fingerprint = _month_fingerprint(event_type, facts, ever_delivered, redelivery)
            previous = previous_states.get(key)
            if previous is None and month in legacy_months:
                # 배포 전의 `monthly:{report_id}` 상태가 이 달을 이미 알고 있다. 새 키에는 현재 모습을
                # 기록만 해 두고 알리지 않는다 — 안 그러면 배포 직후 모든 달이 한꺼번에 다시 나간다.
                previous = _seed_state(event_type, fingerprint, facts, facts_by_report, ever_delivered)
            projection = _project_observed_current(
                facts, observed_at, event_type, fingerprint, redelivery
            )
        except NotificationPayloadError as exc:
            # 한 리포트의 게이트 불일치가 다른 병원의 알림까지 멈추면 안 된다. 이 리포트만
            # 이번 창에서 건너뛰고 로그로 남긴다 — 다음 창에서 다시 시도한다.
            logger.warning("monthly milestone skipped: report=%s reason=%s", facts.report.id, exc)
            continue
        # PDF 재검증 중인 순간(템플릿 갱신 직후)은 준비 완료를 되돌리지 않는다. 되돌리면 검증이
        # 끝나는 순간 '처음 준비 완료'로 읽혀 같은 달의 알림이 다시 나간다.
        sticky = (
            event_type is MonthlyEventType.ARTIFACT_VALIDATION_PENDING
            and previous is not None
            and _phase(previous) == _READY
        )
        states[key] = previous if sticky else fingerprint
        if not sticky and _should_notify(fingerprint, previous, ever_delivered, redelivery):
            changed.append(projection)
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


def _project_current(
    facts: ReportFacts,
    window: ProjectionWindow,
    facts_by_report: dict[uuid.UUID, ReportFacts] | None = None,
) -> MilestoneProjection | None:
    # 같은 숫자를 옮겨 문구·디자인만 바꾼 새 버전(TEMPLATE_REFRESH)은 알리지 않는다.
    if (
        facts_by_report is not None
        and getattr(facts.report, "supersedes_report_id", None) is not None
        and not _numbers_changed_since_prior(facts, facts_by_report)
    ):
        return None
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
    facts: ReportFacts,
    observed_at: datetime,
    event_type: MonthlyEventType,
    fingerprint: str,
    redelivery: bool,
) -> MilestoneProjection:
    """한 달치 현재 상태를 알림 한 건으로 투영한다. stable_id는 (병원, 월, 지문)에서 나온다."""
    return project_monthly_event(
        _monthly_event(
            _MonthlyEventRequest(
                facts,
                event_type,
                observed_at,
                state_uuid(event_type.value, _month_uuid(facts), fingerprint),
                redelivery_needed=redelivery,
            )
        )
    )


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


_READY = "READY"
_NUMBER_FIELDS = (
    "manifest_id",
    "quality",
    "planned_count",
    "success_count",
    "failed_count",
    "excluded_count",
    "sov_summary",
)
_Month = tuple[uuid.UUID, int, int]


def _month_of(facts: ReportFacts) -> _Month:
    return (facts.hospital.id, facts.report.period_year, facts.report.period_month)


def _month_key(facts: ReportFacts) -> str:
    """알림 상태의 키 — 보고서 버전이 아니라 (병원, 계약 월)이다.

    버전마다 키가 달랐을 때는 TEMPLATE_REFRESH 같은 새 버전이 매번 '처음 보는 상태'로 읽혀
    전달 준비 완료 알림이 다시 나갔다. 사람이 아는 단위는 "그 병원의 그 달"이다.
    """
    hospital_id, year, month = _month_of(facts)
    return f"monthly:{hospital_id}:{year:04d}-{month:02d}"


def _month_uuid(facts: ReportFacts) -> uuid.UUID:
    hospital_id, year, month = _month_of(facts)
    return uuid.uuid5(hospital_id, f"monthly:{year:04d}-{month:02d}")


def _phase(value: str) -> str:
    return value.split(":", 1)[0]


def _delivered_months(facts_by_report: dict[uuid.UUID, ReportFacts]) -> set[_Month]:
    """한 번이라도 전달한 달 — 최신 버전이 아니라 어느 버전이든 전달 기록이 유효하면 포함한다."""
    return {_month_of(facts) for facts in facts_by_report.values() if facts.delivered}


def _legacy_state_months(
    facts_by_report: dict[uuid.UUID, ReportFacts], previous_states: dict[str, str]
) -> set[_Month]:
    """이전 형식(`monthly:{report_id}`)의 상태가 이미 알고 있는 달."""
    return {
        _month_of(facts)
        for report_id, facts in facts_by_report.items()
        if f"monthly:{report_id}" in previous_states
    }


def _numbers(report: object) -> tuple[object, ...]:
    return tuple(getattr(report, field, None) for field in _NUMBER_FIELDS)


def _numbers_changed_since_prior(
    facts: ReportFacts, facts_by_report: dict[uuid.UUID, ReportFacts]
) -> bool:
    """이 버전이 대체한 버전과 숫자가 다른가. 문구·디자인만 바꾼 갱신은 같은 숫자를 옮겨 온다."""
    prior_id = getattr(facts.report, "supersedes_report_id", None)
    if prior_id is None:
        return False
    prior = facts_by_report.get(prior_id)
    if prior is None:
        return True
    return _numbers(prior.report) != _numbers(facts.report)


def _month_fingerprint(
    event_type: MonthlyEventType,
    facts: ReportFacts,
    ever_delivered: bool,
    redelivery: bool,
) -> str:
    """Fingerprint what the reader would learn, not every underlying fact.

    표본 수·PDF 행·게이트 코드·보고서 id는 차단이 이어지는 동안에도 계속 움직인다. 그 값을
    지문에 담으면 같은 상태가 관측 창마다 새 상태로 보여 같은 알림이 다시 나간다. 읽는 사람에게
    달라지는 것은 종류(준비 완료/차단/검증 대기)와 전달 이력뿐이다. BLOCKED는 운영자 문구와
    조치 필요 여부를 가르는 값만 더한다.
    """

    if event_type is MonthlyEventType.CUSTOMER_READY:
        token = f":rebuilt:{facts.report.id}" if redelivery else ""
        return f"{_READY}:{ever_delivered}{token}"
    if event_type is MonthlyEventType.BLOCKED:
        closed = facts.manifest is not None and facts.manifest.closed_at is not None
        return f"BLOCKED:{closed}:{'CURRENT_READINESS_BLOCKED' in facts.blockers}"
    return "PENDING"


def _should_notify(
    fingerprint: str, previous: str | None, ever_delivered: bool, redelivery: bool
) -> bool:
    """Notify on a change the reader can act on — not on every new report version.

    준비 완료는 (1) 처음 준비됐을 때 (2) 차단·검증 대기에서 다시 준비됐을 때 (3) 전달 뒤 숫자가
    달라진 새 버전이 준비됐을 때 한 번 알린다. 이미 준비 완료였던 달의 문구·디자인 갱신이나
    전달 기록 변화는 알리지 않는다. 전달한 달이 다시 준비돼도 숫자가 같으면 할 일이 없다.
    """

    if fingerprint == previous:
        return False
    if _phase(fingerprint) != _READY:
        return True
    if previous is not None and _phase(previous) == _READY:
        return redelivery
    return redelivery or not ever_delivered


def _seed_state(
    event_type: MonthlyEventType,
    fingerprint: str,
    facts: ReportFacts,
    facts_by_report: dict[uuid.UUID, ReportFacts],
    ever_delivered: bool,
) -> str:
    """Translate a pre-migration state into the new key without re-announcing it.

    옛 상태값은 보고서 id가 섞인 해시라 단계를 되읽을 수 없다. 현재 모습을 기준으로 삼되, 템플릿
    갱신이 막 PDF 검증을 기다리는 달은 같은 달의 다른 버전이 이미 준비 완료였다면 준비 완료로
    본다 — 검증이 끝나는 순간 다시 알리지 않기 위해서다.
    """

    if event_type is MonthlyEventType.ARTIFACT_VALIDATION_PENDING:
        month = _month_of(facts)
        for other in facts_by_report.values():
            if (
                other.report.id != facts.report.id
                and _month_of(other) == month
                and _current_state(other) is MonthlyEventType.CUSTOMER_READY
            ):
                return f"{_READY}:{ever_delivered}"
    return fingerprint


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
        request.redelivery_needed,
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

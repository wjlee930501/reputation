"""Highest-state onboarding milestone scan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.services.hospital_activation import evaluate_auto_activation
from app.services.hospital_lifecycle import activation_gate_snapshot
from app.services.notification_milestone_messages import MilestoneProjection
from app.services.onboarding_events import (
    OnboardingEvent,
    OnboardingEventType,
    project_onboarding_event,
)
from app.workers.milestone_projection_support import (
    MilestoneStateScan,
    ProjectionWindow,
    event_uuid,
    in_window,
    state_uuid,
)


@dataclass(frozen=True, slots=True)
class _ObservedOnboarding:
    state_key: str
    event: OnboardingEvent
    projection: MilestoneProjection


async def scan_onboarding_milestones(
    db: AsyncSession, window: ProjectionWindow
) -> tuple[MilestoneProjection, ...]:
    """Select only the highest current onboarding milestone per hospital."""

    observations = await _current_observations(db, window.end)
    return tuple(
        item.projection for item in observations if in_window(item.event.occurred_at, window)
    )


async def observe_onboarding_milestones(
    db: AsyncSession,
    observed_at: datetime,
    previous_states: dict[str, str],
) -> MilestoneStateScan:
    """Return current state changes, independent of mutable transition timestamps."""

    observations = await _current_observations(db, observed_at)
    states = {item.state_key: item.projection.stable_id for item in observations}
    milestones = tuple(
        item.projection
        for item in observations
        if previous_states.get(item.state_key) != item.projection.stable_id
    )
    return MilestoneStateScan(milestones, states)


async def _current_observations(
    db: AsyncSession, observed_at: datetime
) -> tuple[_ObservedOnboarding, ...]:

    rows = (
        await db.execute(
            select(Hospital, HospitalHandoff).join(
                HospitalHandoff, HospitalHandoff.hospital_id == Hospital.id
            )
        )
    ).all()
    interval_rows = (
        await db.execute(
            select(
                HospitalServiceInterval.hospital_id,
                func.max(HospitalServiceInterval.started_at),
            )
            .where(HospitalServiceInterval.ended_at.is_(None))
            .group_by(HospitalServiceInterval.hospital_id)
        )
    ).all()
    active_since = {hospital_id: started_at for hospital_id, started_at in interval_rows}
    observations: list[_ObservedOnboarding] = []
    for hospital, handoff in rows:
        event = _current_event(hospital, handoff, active_since.get(hospital.id), observed_at)
        if event is not None:
            observations.append(
                _ObservedOnboarding(
                    f"onboarding:{handoff.id}",
                    event,
                    project_onboarding_event(event),
                )
            )
    return tuple(observations)


def _needs_manual_activation(hospital: Hospital, handoff_accepted: bool) -> bool:
    """사람이 실제로 눌러야 하는 병원만 ACTIVATION_READY를 만든다.

    이미 ACTIVE인 병원은 위쪽 분기에서 걸러지고, 기본 주소만 쓰는 병원은 허브 준비
    태스크가 그대로 운영을 시작하므로 재촉할 대상이 아니다. 남는 것은 자기 도메인이
    지정돼 DNS 확인이 필요한 병원과, 자동 전환 대상이 아닌 상태(PAUSED 등)뿐이다.
    PAUSED는 운영자가 일부러 멈춘 것이므로 활성화를 재촉하지 않는다.
    """
    if hospital.status is HospitalStatus.PAUSED:
        return False
    if not activation_gate_snapshot(hospital, handoff_accepted=handoff_accepted)["ready"]:
        return False
    return evaluate_auto_activation(hospital) is not None


def _current_event(
    hospital: Hospital,
    handoff: HospitalHandoff,
    active_since: datetime | None,
    observed_at: datetime,
) -> OnboardingEvent | None:
    accepted = handoff.state is HandoffState.HANDOFF_ACCEPTED
    if hospital.status is HospitalStatus.ACTIVE:
        occurred_at = active_since or hospital.updated_at
        event_type = OnboardingEventType.HOSPITAL_ACTIVE
    elif _needs_manual_activation(hospital, accepted):
        occurred_at = max(hospital.updated_at, handoff.updated_at)
        event_type = OnboardingEventType.ACTIVATION_READY
    elif accepted and handoff.accepted_at is not None:
        occurred_at = handoff.accepted_at
        event_type = OnboardingEventType.HANDOFF_ACCEPTED
    elif (
        handoff.state is HandoffState.CONTRACTED
        and handoff.sla_due_at is not None
        and handoff.sla_due_at < observed_at
    ):
        occurred_at = handoff.sla_due_at + timedelta(microseconds=1)
        event_type = OnboardingEventType.HANDOFF_OVERDUE
    else:
        return None
    recovered_from = None
    if event_type is OnboardingEventType.HANDOFF_ACCEPTED and handoff.sla_due_at is not None:
        overdue_at = handoff.sla_due_at + timedelta(microseconds=1)
        recovered_from = event_uuid("HANDOFF_OVERDUE", handoff.id, overdue_at)
    event_id = (
        state_uuid(event_type.value, handoff.id, "activation-gate-ready")
        if event_type is OnboardingEventType.ACTIVATION_READY
        else event_uuid(event_type.value, handoff.id, occurred_at)
    )
    return OnboardingEvent(
        event_id,
        event_type,
        hospital.id,
        hospital.name,
        "담당 AE",
        occurred_at,
        handoff.sla_due_at,
        recovered_from,
    )

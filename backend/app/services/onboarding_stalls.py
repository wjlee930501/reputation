"""온보딩이 시간만 흐르고 다음 단계로 가지 않는 병원을 원인별 사고 하나로 올린다.

마일스톤 요약(`onboarding_events`)은 진행을 알리는 Slack이고 사람의 할 일 술어에 잡히지
않는다. 여기는 기한이 지난 정체만 `open_ops_incident`로 열고, 조건이 풀리면 조용히 닫는다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.models.hospital import HospitalStatus

ONBOARDING_STALL_PIPELINE = "onboarding_stall"
ONBOARDING_STALL_OBJECT_TYPE = "hospital"
ONBOARDING_STALL_ACTOR = "system:onboarding-stall-scan"
PROFILE_STALL_AFTER = timedelta(days=3)
SCHEDULE_STALL_AFTER = timedelta(hours=24)
PUBLICATION_STALL_AFTER = timedelta(days=7)
SOURCE_FETCH_STALL_AFTER = timedelta(hours=24)
_KST = ZoneInfo("Asia/Seoul")
_PUBLIC_STATUSES = frozenset({HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN})


class OnboardingStall(StrEnum):
    PROFILE_INCOMPLETE = "ONBOARDING_PROFILE_INCOMPLETE"
    SCHEDULE_MISSING = "ONBOARDING_SCHEDULE_MISSING"
    NO_PUBLICATION = "ONBOARDING_NO_PUBLICATION"
    SOURCES_UNREACHABLE = "ONBOARDING_SOURCES_UNREACHABLE"


@dataclass(frozen=True, slots=True)
class OnboardingFacts:
    hospital_id: uuid.UUID
    hospital_name: str
    status: HospitalStatus
    profile_complete: bool
    schedule_set: bool
    handoff_accepted_at: datetime | None
    essence_current_since: datetime | None
    active_since: datetime | None
    first_schedule_start: date | None
    published_count: int
    required_source_count: int
    channel_source_count: int
    channel_sources_all_error_since: datetime | None


def detect_onboarding_stalls(
    facts: OnboardingFacts,
    now: datetime,
    *,
    source_fetch_alerts: bool,
) -> tuple[OnboardingStall, ...]:
    """지금 사람이 손대야 풀리는 온보딩 정체들. PAUSED는 일부러 멈춘 병원이라 없다."""

    if facts.status == HospitalStatus.PAUSED:
        return ()
    found: list[OnboardingStall] = []
    if (
        facts.handoff_accepted_at is not None
        and not facts.profile_complete
        and now - facts.handoff_accepted_at >= PROFILE_STALL_AFTER
    ):
        found.append(OnboardingStall.PROFILE_INCOMPLETE)
    if (
        facts.status in _PUBLIC_STATUSES
        and not facts.schedule_set
        and facts.essence_current_since is not None
        and now - facts.essence_current_since >= SCHEDULE_STALL_AFTER
    ):
        found.append(OnboardingStall.SCHEDULE_MISSING)
    if (
        facts.status == HospitalStatus.ACTIVE
        and facts.schedule_set
        and facts.published_count == 0
        and facts.active_since is not None
    ):
        started = facts.active_since
        if facts.first_schedule_start is not None:
            started = max(
                started, datetime.combine(facts.first_schedule_start, time.min, tzinfo=_KST)
            )
        if now - started >= PUBLICATION_STALL_AFTER:
            found.append(OnboardingStall.NO_PUBLICATION)
    if (
        source_fetch_alerts
        and facts.required_source_count == 0
        and facts.channel_source_count > 0
        and facts.channel_sources_all_error_since is not None
        and now - facts.channel_sources_all_error_since >= SOURCE_FETCH_STALL_AFTER
    ):
        found.append(OnboardingStall.SOURCES_UNREACHABLE)
    return tuple(found)

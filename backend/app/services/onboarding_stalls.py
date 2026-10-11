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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentItem, ContentSchedule
from app.models.essence import HospitalSourceAsset, SourceStatus
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.services.essence_readiness import get_essence_readiness
from app.services.essence_sources import required_text_source_predicate

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


async def load_onboarding_facts(db: AsyncSession) -> list[OnboardingFacts]:
    """판정에 필요한 사실을 병원별로 모은다. PAUSED도 읽어야 열린 사고를 닫을 수 있다.

    공개 시작은 열린 서비스 구간, 발행은 최초 공개 사실(`first_published_at`), 근거 자료는
    필수 텍스트 자료, 채널 주소 자료는 URL이 있고 제외되지 않은 자료다. Essence 승인 시각은
    공개 상태인데 일정이 없는 병원(정체 후보)만 readiness로 읽는다(권한 변경이면 current가 아님).
    """

    hospitals = (await db.execute(select(Hospital))).scalars().all()
    accepted = dict(
        (await db.execute(select(HospitalHandoff.hospital_id, HospitalHandoff.accepted_at))).all()
    )
    active_since = dict(
        (
            await db.execute(
                select(
                    HospitalServiceInterval.hospital_id,
                    func.max(HospitalServiceInterval.started_at),
                )
                .where(HospitalServiceInterval.ended_at.is_(None))
                .group_by(HospitalServiceInterval.hospital_id)
            )
        ).all()
    )
    schedule_start = dict(
        (
            await db.execute(
                select(ContentSchedule.hospital_id, func.min(ContentSchedule.active_from))
                .where(ContentSchedule.is_active.is_(True))
                .group_by(ContentSchedule.hospital_id)
            )
        ).all()
    )
    published = dict(
        (
            await db.execute(
                select(ContentItem.hospital_id, func.count())
                .where(ContentItem.first_published_at.is_not(None))
                .group_by(ContentItem.hospital_id)
            )
        ).all()
    )
    required = dict(
        (
            await db.execute(
                select(HospitalSourceAsset.hospital_id, func.count())
                .where(required_text_source_predicate())
                .group_by(HospitalSourceAsset.hospital_id)
            )
        ).all()
    )
    channel = {
        hospital_id: (int(total), int(errors), latest)
        for hospital_id, total, errors, latest in (
            await db.execute(
                select(
                    HospitalSourceAsset.hospital_id,
                    func.count(),
                    func.count().filter(HospitalSourceAsset.status == SourceStatus.ERROR),
                    func.max(HospitalSourceAsset.updated_at),
                )
                .where(
                    HospitalSourceAsset.url.is_not(None),
                    HospitalSourceAsset.status != SourceStatus.EXCLUDED,
                )
                .group_by(HospitalSourceAsset.hospital_id)
            )
        ).all()
    }

    facts: list[OnboardingFacts] = []
    for hospital in hospitals:
        essence_since = None
        if hospital.status in _PUBLIC_STATUSES and not hospital.schedule_set:
            current = (await get_essence_readiness(db, hospital.id)).current
            essence_since = getattr(current, "approved_at", None) if current else None
        total, errors, latest = channel.get(hospital.id, (0, 0, None))
        facts.append(
            OnboardingFacts(
                hospital_id=hospital.id,
                hospital_name=hospital.name,
                status=hospital.status,
                profile_complete=bool(hospital.profile_complete),
                schedule_set=bool(hospital.schedule_set),
                handoff_accepted_at=accepted.get(hospital.id),
                essence_current_since=essence_since,
                active_since=active_since.get(hospital.id),
                first_schedule_start=schedule_start.get(hospital.id),
                published_count=int(published.get(hospital.id, 0)),
                required_source_count=int(required.get(hospital.id, 0)),
                channel_source_count=total,
                # 채널 주소가 모두 ERROR일 때 그 중 가장 늦은 실패 시각이 "모두 실패한 시각"이다.
                channel_sources_all_error_since=latest if total and errors == total else None,
            )
        )
    return facts

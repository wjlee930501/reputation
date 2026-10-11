"""온보딩이 시간만 흐르고 다음 단계로 가지 않는 병원을 원인별 사고 하나로 올린다.

마일스톤 요약(`onboarding_events`)은 진행을 알리는 Slack이고 사람의 할 일 술어에 잡히지
않는다. 여기는 기한이 지난 정체만 `open_ops_incident`로 열고, 조건이 풀리면 조용히 닫는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_sessionmaker
from app.models.content import ContentItem, ContentSchedule
from app.models.essence import HospitalSourceAsset, SourceStatus
from app.models.handoff import HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.essence_readiness import get_essence_readiness
from app.services.essence_sources import required_text_source_predicate
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.services.ops_incident_alerts import open_ops_incident, recover_ops_incident

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


# (문제, 고객 영향, 다음 행동, 병원 탭 경로) — 존재하는 탭 이름만 쓴다.
_STALL_COPY: dict[OnboardingStall, tuple[str, str, str, str]] = {
    OnboardingStall.PROFILE_INCOMPLETE: (
        "고객 인수 3일이 지났지만 병원 기본 정보가 완료되지 않았습니다.",
        "병원 공개 페이지와 콘텐츠 운영이 시작되지 않습니다.",
        "병원 정보 화면에서 남은 필수 항목을 채워 주세요.",
        "info",
    ),
    OnboardingStall.SCHEDULE_MISSING: (
        "콘텐츠 운영 기준 승인 뒤 24시간이 지났지만 발행 일정이 만들어지지 않았습니다.",
        "공개 페이지는 열려 있어도 새 글이 만들어지지 않습니다.",
        "콘텐츠 화면에서 발행 일정을 저장해 주세요.",
        "content",
    ),
    OnboardingStall.NO_PUBLICATION: (
        "공개 운영과 일정 시작 뒤 7일이 지났지만 발행된 글이 없습니다.",
        "병원 공개 페이지에 글이 하나도 없습니다.",
        "콘텐츠 화면에서 생성·발행이 멈춘 사유를 확인해 주세요.",
        "content",
    ),
    OnboardingStall.SOURCES_UNREACHABLE: (
        "등록한 채널 주소를 모두 24시간 넘게 읽지 못했고 근거 자료가 없습니다.",
        "콘텐츠 운영 기준을 만들 수 없어 콘텐츠 운영이 시작되지 않습니다.",
        "병원 정보 화면에서 채널 주소를 고치거나 원장 인터뷰 같은 근거 자료를 올려 주세요.",
        "info",
    ),
}
_REOPENABLE = {None, IncidentState.RECOVERED.value}


def _object_id(hospital_id: uuid.UUID, stall: OnboardingStall) -> str:
    return f"{hospital_id}:{stall.value}"


async def _incident_state(object_id: str) -> str | None:
    key = build_incident_key(
        ONBOARDING_STALL_PIPELINE,
        ONBOARDING_STALL_OBJECT_TYPE,
        object_id,
        IncidentFingerprint.MISSING_PREREQUISITE,
    )
    async with get_async_sessionmaker()() as db:
        return await db.scalar(select(Incident.state).where(Incident.dedupe_key == key))


async def apply_onboarding_stalls(
    facts_list: Sequence[OnboardingFacts],
    now: datetime,
    *,
    source_fetch_alerts: bool,
    open_incident: Callable[..., Awaitable[object]] = open_ops_incident,
    recover_incident: Callable[..., Awaitable[bool]] = recover_ops_incident,
    incident_state: Callable[[str], Awaitable[str | None]] = _incident_state,
) -> dict[str, int]:
    """새 정체는 한 번만 열고(확인된 사고는 다시 열지 않는다), 풀린 정체는 조용히 닫는다."""

    opened = recovered = 0
    for facts in facts_list:
        found = set(detect_onboarding_stalls(facts, now, source_fetch_alerts=source_fetch_alerts))
        for stall in OnboardingStall:
            object_id = _object_id(facts.hospital_id, stall)
            if stall in found:
                if await incident_state(object_id) not in _REOPENABLE:
                    continue
                problem, impact, action, tab = _STALL_COPY[stall]
                await open_incident(
                    pipeline=ONBOARDING_STALL_PIPELINE,
                    object_type=ONBOARDING_STALL_OBJECT_TYPE,
                    object_id=object_id,
                    incident_type=stall.value,
                    safe_error_code=stall.value,
                    problem=problem,
                    customer_impact=impact,
                    next_action=action,
                    source_type="ONBOARDING_STALL",
                    hospital_name=facts.hospital_name,
                    hospital_id=facts.hospital_id,
                    admin_path=f"/hospitals/{facts.hospital_id}/{tab}",
                    fingerprint=IncidentFingerprint.MISSING_PREREQUISITE,
                    actor=ONBOARDING_STALL_ACTOR,
                    severity=IncidentSeverity.MEDIUM,
                    notify=True,
                )
                opened += 1
            elif await recover_incident(
                pipeline=ONBOARDING_STALL_PIPELINE,
                object_type=ONBOARDING_STALL_OBJECT_TYPE,
                object_id=object_id,
                fingerprint=IncidentFingerprint.MISSING_PREREQUISITE,
                hospital_name=facts.hospital_name,
                actor=ONBOARDING_STALL_ACTOR,
                reason="onboarding progressed",
                notify=False,
            ):
                recovered += 1
    return {"opened": opened, "recovered": recovered}

"""온보딩 정체 판정의 사실 로더 — 실 Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.content import ContentSchedule
from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.monthly_control import HospitalServiceInterval
from app.services.onboarding_stalls import (
    OnboardingStall,
    detect_onboarding_stalls,
    load_onboarding_facts,
)


async def test_loader_reads_every_fact_the_rules_need(pg_async_session) -> None:
    now = datetime.now(UTC)
    hospital = Hospital(
        id=uuid.uuid4(), name="로더테스트의원", slug=f"stall-loader-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE, site_live=True, site_built=True, profile_complete=True,
        schedule_set=True, plan=Plan.PLAN_12,
    )
    pg_async_session.add(hospital)
    await pg_async_session.flush()
    pg_async_session.add_all([
        HospitalServiceInterval(hospital_id=hospital.id, started_at=now - timedelta(days=10),
                                provenance="ACTIVATION"),
        ContentSchedule(hospital_id=hospital.id, plan="PLAN_12", publish_days=[0, 2, 4],
                        active_from=(now - timedelta(days=9)).date(), is_active=True),
        HospitalSourceAsset(hospital_id=hospital.id, source_type=SourceType.HOMEPAGE,
                            title="홈페이지", url="https://example.invalid/a",
                            status=SourceStatus.ERROR, source_metadata={}),
    ])
    await pg_async_session.flush()

    facts = {f.hospital_id: f for f in await load_onboarding_facts(pg_async_session)}[hospital.id]

    assert facts.status == HospitalStatus.ACTIVE
    assert facts.published_count == 0
    assert facts.active_since is not None and facts.first_schedule_start is not None
    assert facts.required_source_count == 0
    assert facts.channel_source_count == 1
    assert facts.channel_sources_all_error_since is not None
    assert detect_onboarding_stalls(facts, now, source_fetch_alerts=False) == (
        OnboardingStall.NO_PUBLICATION,
    )

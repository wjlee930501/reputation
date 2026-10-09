"""M-13 후속 — 완료 해제와 공개 최소 사실을 실제 세션에서 구분한다.

선택 보강을 비우면 ``profile_complete``는 해제되지만, 공개 최소 사실이 남은 ACTIVE 병원은
계속 안전하게 공개된다. PAUSED 병원도 같은 저장을 허용하되 그 저장이 병원을 재활성화하지
않는다. 완료 여부는 요청 플래그가 아니라 저장된 프로파일 요구사항에서 파생한다.
"""

import uuid

import pytest
from fastapi import BackgroundTasks
from httpx import ASGITransport, AsyncClient

from app.api.admin import hospitals as hospitals_api
from app.core.database import get_db
from app.main import app
from app.models.hospital import Hospital, HospitalStatus


def _hospital_row(*, status: HospitalStatus, site_live: bool) -> Hospital:
    """공개 게이트를 모두 통과하는 완성된 프로파일 행."""
    return Hospital(
        id=uuid.uuid4(),
        name="완료해제테스트의원",
        slug=f"uncomplete-{uuid.uuid4().hex[:8]}",
        status=status,
        address="서울 성동구 성수동",
        phone="02-000-0000",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://clinic.example.com",
        google_maps_url="https://maps.google.com/example",
        naver_place_url="https://naver.me/example",
        latitude=37.5,
        longitude=127.0,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        director_name="김원장",
        director_career="외과 전문의",
        director_philosophy="충분히 설명합니다.",
        treatments=[{"name": "치질 수술", "description": None}],
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=site_live,
        schedule_set=True,
    )


async def _reload(session, hospital_id: uuid.UUID) -> Hospital:
    session.expire_all()
    return await session.get(Hospital, hospital_id)


async def _public_profile(session, slug: str):
    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get(f"/api/v1/public/hospitals/{slug}")
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_optional_enrichment_can_uncomplete_live_hospital_without_hiding_it(
    pg_async_session,
):
    hospital = _hospital_row(status=HospitalStatus.ACTIVE, site_live=True)
    hospital_id = hospital.id
    pg_async_session.add(hospital)
    await pg_async_session.commit()

    before = await _public_profile(pg_async_session, hospital.slug)
    assert before.status_code == 200
    minimum_public_facts = {
        key: before.json()[key] for key in ("name", "slug", "address", "phone", "treatments")
    }

    result = await hospitals_api.update_profile(
        hospital_id,
        hospitals_api.HospitalProfileUpdate(keywords=[]),
        BackgroundTasks(),
        db=pg_async_session,
    )

    stored = await _reload(pg_async_session, hospital_id)
    after = await _public_profile(pg_async_session, hospital.slug)

    assert result["profile_complete"] is False
    assert stored.profile_complete is False
    assert stored.status == HospitalStatus.ACTIVE
    assert stored.site_live is True
    assert after.status_code == 200
    assert {key: after.json()[key] for key in minimum_public_facts} == minimum_public_facts
    assert after.json()["keywords"] == []


@pytest.mark.asyncio
async def test_uncomplete_on_paused_hospital_is_saved(pg_async_session):
    hospital = _hospital_row(status=HospitalStatus.PAUSED, site_live=True)
    hospital_id = hospital.id
    pg_async_session.add(hospital)
    await pg_async_session.commit()

    await hospitals_api.update_profile(
        hospital_id,
        hospitals_api.HospitalProfileUpdate(keywords=[]),
        BackgroundTasks(),
        db=pg_async_session,
    )

    stored = await _reload(pg_async_session, hospital_id)
    assert stored.profile_complete is False
    assert stored.status == HospitalStatus.PAUSED

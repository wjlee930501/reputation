"""M-13 후속 — 실제 세션에서 profile_complete 해제 가드를 확인한다.

단위 테스트의 fake 세션은 409가 아무것도 남기지 않는다는 것을 증명하지 못한다.
여기서는 진짜 Postgres 트랜잭션에서 거절된 PATCH가 행을 그대로 두는지,
일시정지 병원에서는 해제가 실제로 저장되는지를 본다.
"""

import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api
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


@pytest.mark.asyncio
async def test_uncomplete_on_live_hospital_persists_nothing(pg_async_session):
    hospital = _hospital_row(status=HospitalStatus.ACTIVE, site_live=True)
    hospital_id = hospital.id
    pg_async_session.add(hospital)
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(
            hospital_id,
            hospitals_api.HospitalProfileUpdate(profile_complete=False),
            BackgroundTasks(),
            db=pg_async_session,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "PROFILE_COMPLETE_REQUIRED_WHILE_LIVE"

    await pg_async_session.rollback()
    stored = await _reload(pg_async_session, hospital_id)
    assert stored.profile_complete is True
    assert stored.status == HospitalStatus.ACTIVE


@pytest.mark.asyncio
async def test_uncomplete_on_paused_hospital_is_saved(pg_async_session):
    hospital = _hospital_row(status=HospitalStatus.PAUSED, site_live=True)
    hospital_id = hospital.id
    pg_async_session.add(hospital)
    await pg_async_session.commit()

    await hospitals_api.update_profile(
        hospital_id,
        hospitals_api.HospitalProfileUpdate(profile_complete=False),
        BackgroundTasks(),
        db=pg_async_session,
    )

    stored = await _reload(pg_async_session, hospital_id)
    assert stored.profile_complete is False
    assert stored.status == HospitalStatus.PAUSED

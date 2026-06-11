"""P2-10 — profile_complete 병원의 필수 필드 비우기 차단."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api


class FakeDB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status="ONBOARDING",
        site_live=False,
        profile_complete=True,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        director_name="김원장",
        director_career=None,
        director_philosophy=None,
        director_credentials=None,
        address="서울 성동구",
        phone="02-000-0000",
        business_hours=None,
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url=None,
        naver_place_url=None,
        latitude=None,
        longitude=None,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        treatments=[{"name": "치질 수술", "description": None}],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "patch_body,field_name",
    [
        ({"keywords": []}, "keywords"),
        ({"region": []}, "region"),
        ({"specialties": []}, "specialties"),
        ({"address": ""}, "address"),
        ({"director_name": ""}, "director_name"),
    ],
)
async def test_patch_cannot_empty_required_field_on_complete_profile(patch_body, field_name):
    """profile_complete=True가 유지되는 한 필수 필드를 빈 값으로 비울 수 없다 (422)."""
    hospital = _hospital()
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(**patch_body)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 422
    assert field_name in exc.value.detail
    assert "비울 수 없습니다" in exc.value.detail
    assert db.committed is False


async def test_completion_transition_with_missing_fields_keeps_400():
    """미완료 → 완료 전환 시 누락 필드는 기존대로 400."""
    hospital = _hospital(profile_complete=False, keywords=[])
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(profile_complete=True)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 400
    assert "keywords" in exc.value.detail
    assert db.committed is False

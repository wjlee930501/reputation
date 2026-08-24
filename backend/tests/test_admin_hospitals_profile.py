"""P2-10 — profile_complete 병원의 필수 필드 비우기 차단."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api
from app.services.hospital_geocoding import GeocodeResult, GeocodingError


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
        plan=None,
        source_lead_id=None,
        onboarding_note=None,
        site_live=False,
        site_built=False,
        profile_complete=True,
        v0_report_done=False,
        schedule_set=False,
        created_at=None,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        director_name="김원장",
        director_career="외과 전문의",
        director_philosophy="충분히 설명합니다.",
        director_credentials=None,
        address="서울 성동구",
        phone="02-000-0000",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://clinic.example.com",
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url="https://maps.google.com/example",
        naver_place_url="https://naver.me/example",
        aeo_domain=None,
        latitude=37.5,
        longitude=127.0,
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
    "patch_body,requirement_key",
    [
        ({"keywords": []}, "targeting"),
        ({"region": []}, "geo"),
        ({"specialties": []}, "targeting"),
        ({"address": ""}, "contact"),
        ({"director_name": ""}, "director_basic"),
    ],
)
async def test_patch_cannot_empty_required_field_on_complete_profile(patch_body, requirement_key):
    """profile_complete=True가 유지되는 한 필수 필드를 빈 값으로 비울 수 없다 (422)."""
    hospital = _hospital()
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(**patch_body)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 422
    assert requirement_key in exc.value.detail
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
    assert "targeting" in exc.value.detail
    assert db.committed is False


async def test_address_change_geocodes_once_and_persists_coordinates(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)
    calls = []

    async def fake_geocode(address):
        calls.append(address)
        return GeocodeResult(37.566535, 126.977969)

    monkeypatch.setattr(hospitals_api, "geocode_address", fake_geocode)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(address="서울 중구 세종대로 110"),
        BackgroundTasks(),
        db=db,
    )

    assert calls == ["서울 중구 세종대로 110"]
    assert hospital.latitude == 37.566535
    assert hospital.longitude == 126.977969
    assert result["latitude"] == 37.566535


async def test_address_geocode_failure_is_concrete_and_does_not_save(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)

    async def fail_geocode(_address):
        raise GeocodingError("입력한 주소에서 좌표를 찾지 못했습니다.")

    monkeypatch.setattr(hospitals_api, "geocode_address", fail_geocode)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(address="잘못된 주소"),
            BackgroundTasks(),
            db=db,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "ADDRESS_GEOCODE_FAILED"
    assert "좌표를 찾지 못했습니다" in exc.value.detail["message"]
    assert db.committed is False


async def test_advanced_manual_coordinates_skip_address_geocode(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)

    async def unexpected_geocode(_address):
        raise AssertionError("manual coordinates must not call the provider")

    monkeypatch.setattr(hospitals_api, "geocode_address", unexpected_geocode)

    await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(
            address="서울 중구 직접 확인 주소",
            latitude=37.1,
            longitude=127.1,
            geocode_address=False,
        ),
        BackgroundTasks(),
        db=db,
    )

    assert hospital.latitude == 37.1
    assert hospital.longitude == 127.1


def test_list_serializer_includes_custom_domain_for_admin_search():
    hospital = _hospital(aeo_domain="jangclinic.kr", site_built=True, site_live=True)

    payload = hospitals_api._serialize_list(hospital)

    assert payload["aeo_domain"] == "jangclinic.kr"

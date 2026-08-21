"""P2-10 — profile_complete 병원의 필수 필드 비우기 차단."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api
from app.api.admin.accounts import require_active_account


class FakeDB:
    def __init__(self, hospital, media_asset=None):
        self.hospital = hospital
        self.media_asset = media_asset
        self.added = []
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.media_asset)

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
        aeo_domain=None,
        site_live=False,
        site_built=False,
        profile_complete=True,
        v0_report_done=False,
        schedule_set=False,
        created_at=None,
        region=["성동구"],
        specialties=["외과"],
        hero_specialties=[],
        content_focus_topics=[],
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


def _media_asset(source_type: str, asset_kind: str, approved_usage: list[str]):
    return SimpleNamespace(
        source_type=source_type,
        source_metadata={
            "asset_kind": asset_kind,
            "approved_usage": approved_usage,
        },
    )


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


async def test_profile_media_rejects_a_new_external_url():
    """A profile PATCH must not turn an arbitrary remote URL into public media."""
    hospital = _hospital(profile_complete=False, logo_url=None, hero_image_url=None)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(logo_url="https://attacker.example/logo.png")

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "MANAGED_MEDIA_ASSET_REQUIRED"
    assert db.committed is False


async def test_profile_media_accepts_the_hospitals_public_managed_asset_url():
    """A hospital may select an existing public onboarding photo as profile media."""
    hospital = _hospital(profile_complete=False, logo_url=None, hero_image_url=None)
    source_id = uuid.uuid4()
    db = FakeDB(
        hospital,
        media_asset=_media_asset("PHOTO_BRAND", "VERIFIED_BRAND_GRAPHIC", ["LOGO", "HERO"]),
    )
    managed_url = f"/api/v1/public/hospitals/{hospital.slug}/assets/{source_id}"
    body = hospitals_api.HospitalProfileUpdate(logo_url=managed_url)

    await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert hospital.logo_url == managed_url
    assert db.committed is True


async def test_profile_hero_accepts_a_verified_facility_asset_with_hero_usage():
    """A verified facility photo explicitly approved for HERO may become the hero image."""
    hospital = _hospital(profile_complete=False, logo_url=None, hero_image_url=None)
    source_id = uuid.uuid4()
    db = FakeDB(
        hospital,
        media_asset=_media_asset("PHOTO_CLINIC_INTERIOR", "VERIFIED_FACILITY", ["HERO", "GALLERY"]),
    )
    managed_url = f"/api/v1/public/hospitals/{hospital.slug}/assets/{source_id}"
    body = hospitals_api.HospitalProfileUpdate(hero_image_url=managed_url)

    await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert hospital.hero_image_url == managed_url
    assert db.committed is True


async def test_profile_logo_rejects_a_doctor_identity_asset():
    """A doctor identity photo cannot be repurposed as a hospital logo."""
    hospital = _hospital(profile_complete=False, logo_url=None, hero_image_url=None)
    source_id = uuid.uuid4()
    db = FakeDB(
        hospital,
        media_asset=_media_asset("PHOTO_DOCTOR", "VERIFIED_REAL_PERSON", ["DOCTOR_IDENTITY"]),
    )
    managed_url = f"/api/v1/public/hospitals/{hospital.slug}/assets/{source_id}"
    body = hospitals_api.HospitalProfileUpdate(logo_url=managed_url)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "MANAGED_MEDIA_ASSET_REQUIRED"
    assert db.committed is False


async def test_profile_media_rejects_an_unavailable_managed_asset_url():
    """A syntactically valid path is not enough without a public owned source asset."""
    hospital = _hospital(profile_complete=False, logo_url=None, hero_image_url=None)
    managed_url = f"/api/v1/public/hospitals/{hospital.slug}/assets/{uuid.uuid4()}"
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(hero_image_url=managed_url)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "MANAGED_MEDIA_ASSET_REQUIRED"
    assert db.committed is False


async def test_profile_media_keeps_an_unchanged_legacy_value():
    """Existing profile media remains editable until an operator deliberately replaces it."""
    legacy_url = "https://legacy.example/logo.png"
    hospital = _hospital(profile_complete=False, logo_url=legacy_url, hero_image_url=None)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(logo_url=legacy_url)

    await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert hospital.logo_url == legacy_url
    assert db.committed is True


async def test_profile_update_persists_director_requested_display_and_content_focus() -> None:
    hospital = _hospital(profile_complete=False)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(
        hero_specialties=["정형외과", " 통증의학과 ", "외상치료"],
        hero_description="매일 365 야간진료",
        content_focus_topics=["정형외과", "신경외과", "통증의학과", "외상"],
    )

    payload = await hospitals_api.update_profile(
        hospital.id,
        body,
        BackgroundTasks(),
        db=db,
    )

    assert payload["hero_specialties"] == ["정형외과", "통증의학과", "외상치료"]
    assert payload["hero_description"] == "매일 365 야간진료"
    assert payload["content_focus_topics"] == ["정형외과", "신경외과", "통증의학과", "외상"]
    assert db.committed is True


def test_profile_update_requires_an_active_account():
    """The profile-media trust boundary is not reachable without an active Admin account."""
    route = next(
        route
        for route in hospitals_api.router.routes
        if getattr(route, "path", None) == "/admin/hospitals/{hospital_id}/profile"
    )

    assert any(dependency.call is require_active_account for dependency in route.dependant.dependencies)


def test_list_serializer_includes_custom_domain_for_admin_search():
    hospital = _hospital(aeo_domain="jangclinic.kr", site_built=True, site_live=True)

    payload = hospitals_api._serialize_list(hospital)

    assert payload["aeo_domain"] == "jangclinic.kr"

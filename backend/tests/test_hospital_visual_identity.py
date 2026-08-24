import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.api.admin.hospitals import HospitalProfileUpdate, _serialize, update_profile
from app.services.hospital_logo import EXTERNAL_LOGO_URL_MESSAGE

VISUAL_FIELDS = (
    "brand_primary_color",
    "logo_url",
    "hero_image_url",
    "hero_media_kind",
    "hero_headline",
    "hero_description",
    "image_style_direction",
    "site_access_mode",
)


def test_visual_identity_profile_accepts_approved_theme_copy_and_generation_direction():
    profile = HospitalProfileUpdate(
        brand_primary_color="#d6a72c",
        brand_accent_color="#6f8a56",
        hero_headline="오늘도 문 여는 동네 주치의\n방문 전에 진료 정보를 확인하세요",
        hero_description="지역 가족이 필요한 진료 범위와 시간을 안내합니다.",
        image_style_direction="밝은 자연광과 손그림 질감, 아이보리와 연두 포인트",
        site_access_mode="urgent",
        hero_media_kind="VERIFIED_FACILITY",
    )

    assert profile.brand_primary_color == "#D6A72C"
    assert profile.site_access_mode == "urgent"
    assert profile.hero_headline.startswith("오늘도 문 여는")


@pytest.mark.parametrize("field", ["brand_primary_color", "brand_accent_color"])
def test_visual_identity_profile_rejects_malformed_colors(field: str):
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(**{field: "gold"})


def test_visual_identity_profile_rejects_medical_advertising_superlatives():
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(hero_headline="국내 최초 완치 치료를 약속합니다")


def test_choosing_automatic_selection_clears_the_field_instead_of_failing_the_save():
    """The profile screen PATCHes every field in one request.

    When an operator resets '자동 선택' the browser sends an empty string. Rejecting
    it used to 422 the entire request, so the color, copy and art direction sent in
    the same save were silently discarded too.
    """
    profile = HospitalProfileUpdate(
        brand_primary_color="#006772",
        hero_headline="야간·주말 진료 시간을\n방문 전에 확인하세요",
        site_access_mode="",
        hero_media_kind="",
    )

    payload = profile.model_dump(exclude_unset=True)
    assert payload["site_access_mode"] is None
    assert payload["hero_media_kind"] is None
    assert payload["brand_primary_color"] == "#006772"
    assert payload["hero_headline"].startswith("야간·주말")


def test_blank_visual_text_and_urls_clear_rather_than_reject():
    payload = HospitalProfileUpdate(
        brand_primary_color="",
        logo_url="",
        hero_image_url="",
        hero_headline="",
        hero_description="",
    ).model_dump(exclude_unset=True)

    assert all(payload[field] is None for field in payload)


def test_every_visual_field_survives_the_admin_round_trip():
    """Fields an operator can set must come back from the detail endpoint unchanged."""
    submitted = HospitalProfileUpdate(
        brand_primary_color="#006772",
        logo_url="https://cdn.example.com/logo.png",
        hero_image_url="https://cdn.example.com/hero.jpg",
        hero_media_kind="BRAND_GRAPHIC",
        hero_headline="야간·주말 진료 시간을\n방문 전에 확인하세요",
        hero_description="평일 09:00 ~ 21:00 진료합니다.",
        image_style_direction="차분한 청록 톤, 실제 진료 공간에 가까운 조명",
        site_access_mode="urgent",
    ).model_dump(exclude_unset=True)

    assert set(submitted) == set(VISUAL_FIELDS)

    hospital = _FakeHospital(**submitted)
    serialized = _serialize(hospital)

    for field in VISUAL_FIELDS:
        assert serialized[field] == submitted[field]


async def test_saving_the_profile_persists_and_clears_visual_fields():
    """The write path, not just validation: values land on the row and clear again."""
    hospital = _FakeHospital(id=uuid.uuid4(), profile_complete=False)
    db = _FakeDB(hospital)

    await update_profile(
        hospital.id,
        HospitalProfileUpdate(
            brand_primary_color="#006772",
            site_access_mode="urgent",
            hero_media_kind="BRAND_GRAPHIC",
            hero_headline="야간·주말 진료 시간을\n방문 전에 확인하세요",
            image_style_direction="차분한 청록 톤",
        ),
        BackgroundTasks(),
        db=db,
    )

    assert hospital.brand_primary_color == "#006772"
    assert hospital.site_access_mode == "urgent"
    assert hospital.hero_media_kind == "BRAND_GRAPHIC"
    assert _serialize(hospital)["hero_headline"].startswith("야간·주말")

    # Resetting the dropdowns to '자동 선택' clears them and leaves the rest intact.
    await update_profile(
        hospital.id,
        HospitalProfileUpdate(site_access_mode="", hero_media_kind=""),
        BackgroundTasks(),
        db=db,
    )

    assert hospital.site_access_mode is None
    assert hospital.hero_media_kind is None
    assert hospital.brand_primary_color == "#006772"
    assert hospital.image_style_direction == "차분한 청록 톤"


class _FakeHospital:
    """Minimal stand-in with the attributes the admin serializer reads."""

    _DEFAULTS = {
        "id": "hospital-id",
        "name": "노원탑365의원",
        "slug": "noweontab365yiweon",
        "plan": "PLAN_16",
        "status": "ACTIVE",
        "region": [],
        "specialties": [],
        "keywords": [],
        "competitors": [],
        "treatments": [],
        "business_hours": {},
        "profile_complete": True,
        "v0_report_done": False,
        "site_built": False,
        "site_live": False,
        "schedule_set": False,
    }

    def __init__(self, **overrides):
        for key, value in {**self._DEFAULTS, **overrides}.items():
            setattr(self, key, value)

    def __getattr__(self, name: str):
        # Unset profile columns read as empty, like a freshly created hospital row.
        return None


class _FakeDB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.committed = False

    async def get(self, _model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    def add(self, _item):
        pass

    async def commit(self):
        self.committed = True

    async def refresh(self, _item):
        pass


def _active_complete_hospital(**overrides):
    values = dict(
        id=uuid.uuid4(),
        profile_complete=True,
        director_name="김원장",
        director_career="정형외과 전문의",
        director_philosophy="필요한 진료를 충분히 설명합니다.",
        address="서울 노원구 동일로 123",
        phone="02-000-0000",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://clinic.example",
        naver_place_url="https://naver.me/example",
        google_maps_url="https://maps.google.com/example",
        latitude=37.65,
        longitude=127.06,
        region=["노원구"],
        specialties=["정형외과"],
        keywords=["관절 통증"],
        treatments=[{"name": "척추·관절 진료"}],
        logo_url="https://legacy-cdn.example/logo.png",
    )
    return _FakeHospital(**{**values, **overrides})


async def test_active_complete_profile_saves_unrelated_edits_with_unchanged_legacy_logo():
    """A full-form PATCH must not make a legacy logo block every later edit."""
    legacy_logo_url = "https://legacy-cdn.example/logo.png"
    hospital = _active_complete_hospital(logo_url=legacy_logo_url)
    db = _FakeDB(hospital)

    await update_profile(
        hospital.id,
        HospitalProfileUpdate(
            profile_complete=True,
            logo_url=legacy_logo_url,
            specialties=["정형외과", "마취통증의학과", "응급의학과"],
            hero_description="척추·관절 통증부터 경증 응급까지 진료합니다.",
        ),
        BackgroundTasks(),
        db=db,
    )

    assert db.committed is True
    assert _serialize(hospital)["specialties"] == ["정형외과", "마취통증의학과", "응급의학과"]
    assert _serialize(hospital)["hero_description"] == "척추·관절 통증부터 경증 응급까지 진료합니다."
    assert hospital.logo_url == legacy_logo_url


async def test_active_complete_profile_rejects_changing_logo_to_new_external_url():
    hospital = _active_complete_hospital()
    db = _FakeDB(hospital)

    with pytest.raises(HTTPException) as exc:
        await update_profile(
            hospital.id,
            HospitalProfileUpdate(logo_url="https://new-cdn.example/logo.png"),
            BackgroundTasks(),
            db=db,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == {
        "code": "EXTERNAL_LOGO_URL",
        "message": EXTERNAL_LOGO_URL_MESSAGE,
    }
    assert db.committed is False
    assert hospital.logo_url == "https://legacy-cdn.example/logo.png"

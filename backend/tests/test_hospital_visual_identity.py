import pytest
from pydantic import ValidationError

from app.api.admin.hospitals import HospitalProfileUpdate


def test_visual_identity_profile_accepts_approved_theme_copy_and_generation_direction():
    profile = HospitalProfileUpdate(
        brand_primary_color="#d6a72c",
        brand_accent_color="#6f8a56",
        hero_headline="오늘도 문 여는 동네 주치의\n방문 전에 진료 정보를 확인하세요",
        hero_description="지역 가족이 필요한 진료 범위와 시간을 안내합니다.",
        image_style_direction="밝은 자연광과 손그림 질감, 아이보리와 연두 포인트",
        site_access_mode="urgent",
        hero_media_kind="VERIFIED_FACILITY",
        hero_specialties=[" 정형외과 ", "통증의학과", "정형외과", "외상치료"],
        content_focus_topics=["정형외과", "신경외과", "통증의학과", "외상"],
    )

    assert profile.brand_primary_color == "#D6A72C"
    assert profile.site_access_mode == "urgent"
    assert profile.hero_headline.startswith("오늘도 문 여는")
    assert profile.hero_specialties == ["정형외과", "통증의학과", "외상치료"]
    assert profile.content_focus_topics == ["정형외과", "신경외과", "통증의학과", "외상"]


@pytest.mark.parametrize("field", ["brand_primary_color", "brand_accent_color"])
def test_visual_identity_profile_rejects_malformed_colors(field: str):
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(**{field: "gold"})


def test_visual_identity_profile_rejects_medical_advertising_superlatives():
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(hero_headline="국내 최초 완치 치료를 약속합니다")


def test_visual_identity_profile_rejects_too_many_custom_topics():
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(hero_specialties=[f"진료영역 {index}" for index in range(9)])


def test_visual_identity_profile_rejects_more_than_three_hero_specialties():
    with pytest.raises(ValidationError, match="hero specialties accept at most 3 items"):
        HospitalProfileUpdate(
            hero_specialties=["정형외과", "통증의학과", "외상치료", "재활의학과"]
        )


def test_visual_identity_profile_rejects_oversized_custom_topic_label():
    with pytest.raises(ValidationError, match="custom topic labels accept at most 40 characters"):
        HospitalProfileUpdate(content_focus_topics=["주" * 41])

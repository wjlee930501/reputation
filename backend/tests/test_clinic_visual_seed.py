"""Verified clinic visual values only: no invented colors, copy, or logos."""

from types import SimpleNamespace

from app.utils.medical_filter import check_forbidden
from app.utils.seed_clinic_visual_identity import (
    VERIFIED_CLINIC_VISUALS,
    availability_facts,
    compose_availability_hero_copy,
    plan_clinic_visual_changes,
)

NOWON_HOURS = {
    "mon": "09:00 ~ 21:00",
    "tue": "09:00 ~ 21:00",
    "wed": "09:00 ~ 21:00",
    "thu": "09:00 ~ 21:00",
    "fri": "09:00 ~ 21:00",
    "sat": "09:00 ~ 17:00",
    "sun": "09:00 ~ 17:00",
}


def _identity(slug: str):
    return next(item for item in VERIFIED_CLINIC_VISUALS if item.slug == slug)


def _hospital(**overrides):
    defaults = {
        "brand_primary_color": None,
        "brand_accent_color": None,
        "site_access_mode": None,
        "hero_headline": None,
        "hero_description": None,
        "business_hours": None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def test_nowon_gets_its_official_primary_color_and_availability_first_access():
    changes = plan_clinic_visual_changes(
        _hospital(business_hours=NOWON_HOURS), _identity("noweontab365yiweon")
    )

    assert changes["brand_primary_color"] == "#006772"
    assert changes["site_access_mode"] == "urgent"


def test_nowon_hero_surfaces_availability_facts_instead_of_a_long_title():
    headline, description = compose_availability_hero_copy(NOWON_HOURS)

    assert headline.count("\n") == 1
    assert len(headline) <= 40
    assert "야간" in headline
    assert "주말" in headline
    # The factual hours live in the description, taken from the stored profile only.
    assert "평일 09:00 ~ 21:00" in description
    assert "토요일 09:00 ~ 17:00" in description
    assert not check_forbidden(headline)
    assert not check_forbidden(description)


def test_hero_copy_is_skipped_when_stored_hours_cannot_support_the_claim():
    assert compose_availability_hero_copy(None) is None
    assert compose_availability_hero_copy({}) is None
    assert compose_availability_hero_copy({"mon": "휴진", "sat": "휴진"}) is None
    # Weekday-daytime-only hours support neither a night nor a weekend claim.
    assert compose_availability_hero_copy({"mon": "09:00 ~ 18:00"}) is None


def test_availability_facts_follow_the_stored_hours():
    assert availability_facts({"mon": "09:00 ~ 21:00"}) == ("야간",)
    assert availability_facts({"sat": "09:00 ~ 13:00", "sun": "09:00 ~ 13:00"}) == ("주말",)
    assert availability_facts({"sat": "09:00 ~ 13:00", "sun": "휴진"}) == ("토요일",)
    assert availability_facts({"mon": "09:00 ~ 18:00"}) == ()


def test_seed_never_invents_a_color_for_a_clinic_without_a_verified_one():
    for slug in (
        "yeonsesogsiweonnaegwayiweon",
        "seoulwnaegwayiweon-wiryejeom",
        "gangsimjangnaegwayiweon",
        "jangpyeonhanoegwayiweon",
    ):
        identity = _identity(slug)
        assert identity.brand_primary_color is None
        assert plan_clinic_visual_changes(_hospital(), identity) == {}


def test_seed_keeps_a_stored_primary_color_and_retires_the_second_accent():
    hospital = _hospital(brand_primary_color="#17365D", brand_accent_color="#B79045")

    changes = plan_clinic_visual_changes(hospital, _identity("jangpyeonhanoegwayiweon"))

    assert changes == {"brand_accent_color": None}


def test_seed_is_idempotent_once_applied():
    applied = _hospital(
        brand_primary_color="#006772",
        site_access_mode="urgent",
        business_hours=NOWON_HOURS,
    )
    headline, description = compose_availability_hero_copy(NOWON_HOURS)
    applied.hero_headline = headline
    applied.hero_description = description

    assert plan_clinic_visual_changes(applied, _identity("noweontab365yiweon")) == {}


def test_seed_requires_no_photos_and_never_touches_the_logo():
    identity = _identity("noweontab365yiweon")
    planned = plan_clinic_visual_changes(_hospital(business_hours=NOWON_HOURS), identity)

    assert "logo_url" not in identity.__slots__
    assert "logo_url" not in planned
    assert "hero_image_url" not in planned


def test_every_onboarded_clinic_has_a_recorded_evidence_note():
    assert len(VERIFIED_CLINIC_VISUALS) == 6
    for identity in VERIFIED_CLINIC_VISUALS:
        assert identity.evidence
        if identity.brand_primary_color is not None:
            assert identity.brand_primary_color.startswith("#")
            assert len(identity.brand_primary_color) == 7


def test_clinic_with_unverified_photos_still_gets_its_verified_color():
    """Photo provenance and brand color are separate decisions."""
    identity = _identity("haengbogdeurimyiweon")

    assert plan_clinic_visual_changes(_hospital(), identity) == {
        "brand_primary_color": "#D6A72C"
    }

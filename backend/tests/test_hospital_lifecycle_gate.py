"""병원 기본 정보 완료 게이트 — D31(2026-10-14): 진료 철학·Google 병원 정보만 선택이다."""

from types import SimpleNamespace

from app.core.config import Settings
from app.services.hospital_lifecycle import (
    missing_profile_requirement_keys,
    profile_requirements,
)

_ALL_KEYS = [
    "director_basic",
    "director_philosophy",
    "contact",
    "web_channels",
    "ai_channels",
    "geo",
    "targeting",
    "treatments",
]


def _hospital(**overrides):
    values = dict(
        director_name="김원장", director_career="외과 전문의", director_philosophy=None,
        address="서울", phone="02-1", business_hours={"mon": "09:00-18:00"},
        website_url="https://a.example", blog_url=None,
        naver_place_url="https://naver.me/x", google_maps_url=None,
        google_business_profile_url=None, latitude=37.5, longitude=127.0,
        region=["강남"], specialties=["외과"], keywords=["치질"],
        treatments=[{"name": "치질 수술"}],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_philosophy_and_google_are_optional() -> None:
    assert missing_profile_requirement_keys(_hospital()) == []


def test_director_career_stays_required() -> None:
    assert missing_profile_requirement_keys(_hospital(director_career=None)) == ["director_basic"]
    assert missing_profile_requirement_keys(_hospital(director_career="  ")) == ["director_basic"]
    assert missing_profile_requirement_keys(_hospital(director_name=None)) == ["director_basic"]


def test_naver_place_stays_required_even_with_google() -> None:
    hospital = _hospital(naver_place_url=None, google_maps_url="https://maps.google.com/x")
    assert missing_profile_requirement_keys(hospital) == ["ai_channels"]


def test_every_other_requirement_is_still_required() -> None:
    assert set(
        missing_profile_requirement_keys(
            _hospital(
                address=None,
                website_url=None,
                latitude=None,
                keywords=[],
                treatments=[],
            )
        )
    ) == {"contact", "web_channels", "geo", "targeting", "treatments"}


def test_key_set_is_unchanged_for_admin_anchors() -> None:
    requirements = profile_requirements(_hospital(director_philosophy=None))
    assert [item.key for item in requirements] == _ALL_KEYS
    philosophy = next(item for item in requirements if item.key == "director_philosophy")
    assert philosophy.passed is True
    assert "선택" in philosophy.label


def test_retired_flag_is_gone_from_settings() -> None:
    assert "PROFILE_GATE_OPTIONAL_ENRICHMENT" not in Settings.model_fields

from types import SimpleNamespace

import pytest

from app.services.hospital_lifecycle import (
    missing_live_prerequisite_keys,
    missing_profile_requirement_keys,
)


def _complete_hospital(**overrides):
    values = {
        "director_name": "김원장",
        "director_career": "외과 전문의",
        "director_philosophy": "충분히 설명합니다.",
        "address": "서울시 성동구",
        "phone": "02-000-0000",
        "business_hours": {"mon": "09:00-18:00"},
        "website_url": "https://clinic.example.com",
        "blog_url": None,
        "naver_place_url": "https://naver.me/example",
        "google_maps_url": "https://maps.google.com/example",
        "google_business_profile_url": None,
        "latitude": 37.5,
        "longitude": 127.0,
        "region": ["성동구"],
        "specialties": ["외과"],
        "keywords": ["치질"],
        "treatments": [{"name": "치질 진료"}],
        "profile_complete": True,
        "v0_report_done": True,
        "site_built": True,
        "handoff_accepted": True,
        "schedule_set": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_active_gate_requires_schedule_after_complete_profile():
    hospital = _complete_hospital(schedule_set=False)
    assert missing_profile_requirement_keys(hospital) == []
    assert missing_live_prerequisite_keys(hospital) == ["schedule_set"]


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(float("nan"), 127.0), (91.0, 127.0), (37.5, 181.0), (True, 127.0)],
)
def test_profile_rejects_invalid_coordinates(latitude, longitude):
    hospital = _complete_hospital(latitude=latitude, longitude=longitude)
    assert "geo" in missing_profile_requirement_keys(hospital)


def test_active_gate_returns_canonical_order():
    hospital = _complete_hospital(
        handoff_accepted=False,
        profile_complete=False,
        v0_report_done=False,
        site_built=False,
        schedule_set=False,
    )
    assert missing_live_prerequisite_keys(hospital) == [
        "handoff_accepted",
        "profile_complete",
        "v0_report_done",
        "site_built",
        "schedule_set",
    ]

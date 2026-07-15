"""Single-source lifecycle gates for hospital onboarding.

The public site can go live after the profile, V0 report, and site preparation
steps.  Content scheduling and the approved writing standard are operational
gates, not domain/TLS gates; keeping the two separate preserves the documented
eight-step onboarding order.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from app.models.hospital import Hospital


@dataclass(frozen=True)
class ProfileRequirement:
    key: str
    label: str
    passed: bool


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_business_hours(value: Any) -> bool:
    return isinstance(value, dict) and any(_text(item) for item in value.values())


def _has_named_treatment(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return any(_text(item.get("name")) if isinstance(item, dict) else _text(item) for item in value)


def profile_requirements(hospital: Hospital) -> list[ProfileRequirement]:
    """Return the authoritative profile-completion checklist.

    This mirrors the Admin checklist.  The API deliberately owns the final
    decision so a handcrafted request cannot mark a partial profile complete.
    """

    has_google = _text(hospital.google_maps_url) or _text(hospital.google_business_profile_url)
    latitude = hospital.latitude
    longitude = hospital.longitude
    has_coordinates = (
        isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and math.isfinite(latitude)
        and -90 <= latitude <= 90
        and isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and math.isfinite(longitude)
        and -180 <= longitude <= 180
    )
    return [
        ProfileRequirement(
            "director_basic",
            "원장명·약력",
            _text(hospital.director_name) and _text(hospital.director_career),
        ),
        ProfileRequirement("director_philosophy", "진료 철학", _text(hospital.director_philosophy)),
        ProfileRequirement(
            "contact",
            "주소·전화번호·진료시간",
            _text(hospital.address)
            and _text(hospital.phone)
            and _has_business_hours(hospital.business_hours),
        ),
        ProfileRequirement(
            "web_channels",
            "홈페이지 또는 블로그",
            _text(hospital.website_url) or _text(hospital.blog_url),
        ),
        ProfileRequirement(
            "ai_channels",
            "네이버 플레이스·Google 병원 정보",
            _text(hospital.naver_place_url) and has_google,
        ),
        ProfileRequirement(
            "geo",
            "좌표·지역 정보",
            has_coordinates and bool(hospital.region),
        ),
        ProfileRequirement(
            "targeting",
            "전문과목·핵심 키워드",
            bool(hospital.specialties) and bool(hospital.keywords),
        ),
        ProfileRequirement("treatments", "진료 항목", _has_named_treatment(hospital.treatments)),
    ]


def missing_profile_requirement_keys(hospital: Hospital) -> list[str]:
    return [
        requirement.key for requirement in profile_requirements(hospital) if not requirement.passed
    ]


def missing_profile_requirement_labels(hospital: Hospital) -> list[str]:
    return [
        requirement.label
        for requirement in profile_requirements(hospital)
        if not requirement.passed
    ]


def missing_live_prerequisite_keys(hospital: Hospital) -> list[str]:
    """STEP 5 gate: scheduling intentionally belongs to STEP 6."""

    return [
        key
        for key, ready in (
            ("profile_complete", hospital.profile_complete),
            ("v0_report_done", hospital.v0_report_done),
            ("site_built", hospital.site_built),
        )
        if not ready
    ]


def missing_live_prerequisite_labels(hospital: Hospital) -> list[str]:
    labels = {
        "profile_complete": "프로파일 완료",
        "v0_report_done": "V0 리포트",
        "site_built": "병원 정보 허브 빌드",
    }
    return [labels[key] for key in missing_live_prerequisite_keys(hospital)]

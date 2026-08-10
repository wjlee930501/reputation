"""Single-source lifecycle gates for hospital onboarding and resumption."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital


@dataclass(frozen=True, slots=True)
class ProfileRequirement:
    key: str
    label: str
    passed: bool


@dataclass(frozen=True, slots=True)
class ActivationRequirement:
    key: str
    label: str
    action: str
    passed: bool


class ActivationRequirementSnapshot(TypedDict):
    key: str
    label: str
    action: str
    passed: bool


class ActivationGateSnapshot(TypedDict):
    ready: bool
    missing: list[str]
    prerequisites: list[ActivationRequirementSnapshot]


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


def activation_requirements(
    hospital: Hospital, *, handoff_accepted: bool
) -> list[ActivationRequirement]:
    """Return ACTIVE prerequisites in the canonical operator order."""

    return [
        ActivationRequirement(
            "handoff_accepted", "고객 인계 승인", "계약·인계 정보를 승인하세요.", handoff_accepted
        ),
        ActivationRequirement(
            "profile_complete", "병원 기본 정보 완료", "병원 기본 정보의 필수 항목을 완료하세요.", hospital.profile_complete
        ),
        ActivationRequirement(
            "v0_report_done", "초기 진단 리포트", "초기 진단 리포트 생성을 완료하세요.", hospital.v0_report_done
        ),
        ActivationRequirement(
            "site_built", "콘텐츠 허브 준비", "AI 노출 콘텐츠 허브 준비를 완료하세요.", hospital.site_built
        ),
        ActivationRequirement(
            "schedule_set", "콘텐츠 발행 일정", "요금제와 발행 요일을 저장하세요.", hospital.schedule_set
        ),
    ]


def missing_live_prerequisite_keys(
    hospital: Hospital, *, handoff_accepted: bool | None = None
) -> list[str]:
    """Return missing ACTIVE prerequisites without treating essence as a gate."""

    accepted = (
        bool(getattr(hospital, "handoff_accepted", False))
        if handoff_accepted is None
        else handoff_accepted
    )
    return [
        requirement.key
        for requirement in activation_requirements(hospital, handoff_accepted=accepted)
        if not requirement.passed
    ]


def activation_gate_snapshot(
    hospital: Hospital, *, handoff_accepted: bool
) -> ActivationGateSnapshot:
    requirements = activation_requirements(hospital, handoff_accepted=handoff_accepted)
    missing = [requirement.key for requirement in requirements if not requirement.passed]
    return {
        "ready": not missing,
        "missing": missing,
        "prerequisites": [
            {
                "key": requirement.key,
                "label": requirement.label,
                "action": requirement.action,
                "passed": requirement.passed,
            }
            for requirement in requirements
        ],
    }


async def evaluate_activation_gate(
    db: AsyncSession, hospital: Hospital
) -> ActivationGateSnapshot:
    """Evaluate the authoritative server gate from persisted handoff state."""

    state = await db.scalar(
        select(HospitalHandoff.state).where(HospitalHandoff.hospital_id == hospital.id)
    )
    return activation_gate_snapshot(
        hospital, handoff_accepted=state is HandoffState.HANDOFF_ACCEPTED
    )


def activation_gate_error(
    snapshot: ActivationGateSnapshot,
) -> dict[str, str | list[str] | list[ActivationRequirementSnapshot]]:
    """Return the stable machine-readable operator blocker payload."""

    return {
        "code": "ACTIVATION_PREREQUISITES_MISSING",
        "message": "공개 운영 시작 전 필수 단계를 완료해 주세요.",
        "missing": snapshot["missing"],
        "prerequisites": snapshot["prerequisites"],
    }

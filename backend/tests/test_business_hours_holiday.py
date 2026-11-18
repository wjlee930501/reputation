"""공휴일 진료시간 — business_hours의 `holiday` 칸(2026-11-18 마케팅 요청).

365서귀포삼성정형외과 원장님 요청: 공휴일 휴진이 AI가 읽는 구조화 데이터에 없어 병원 이름의
'365' 때문에 연중무휴로 오해받는다. 요일 키와 같은 문자열 하나로 저장해 공개 payload가 그대로
싣고, 공개 사이트가 JSON-LD dayOfWeek PublicHolidays로 낸다.
"""

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError

from app.api.admin.hospitals import HospitalProfileUpdate, update_profile
from app.api.public.site import _serialize_hospital
from app.services.hospital_lifecycle import _has_business_hours
from tests.test_hospital_visual_identity import _active_complete_hospital, _FakeDB
from tests.test_public_site import _hospital_with_photo

SEVEN_DAYS = {day: "08:30 ~ 18:00" for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def test_profile_update_keeps_the_holiday_value():
    profile = HospitalProfileUpdate(business_hours={**SEVEN_DAYS, "holiday": "휴진"})
    dumped = profile.model_dump(exclude_unset=True)
    assert dumped["business_hours"]["holiday"] == "휴진"
    assert dumped["business_hours"]["sun"] == "08:30 ~ 18:00"


def test_an_unsent_holiday_is_not_written_as_null():
    # 옛 Admin 화면은 holiday를 보내지 않는다 — null로 저장되면 공개 payload 검증(문자열만)이 깨진다.
    dumped = HospitalProfileUpdate(business_hours={"mon": "09:00-18:00"}).model_dump(exclude_unset=True)
    assert dumped["business_hours"] == {"mon": "09:00-18:00"}


def test_holiday_value_length_is_bounded_like_weekdays():
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(business_hours={"holiday": "가" * 101})


def test_holiday_alone_does_not_satisfy_the_business_hours_requirement():
    assert _has_business_hours({"holiday": "휴진"}) is False
    assert _has_business_hours({"holiday": "휴진", "mon": "09:00-18:00"}) is True
    assert _has_business_hours({"mon": "09:00-18:00"}) is True
    assert _has_business_hours({"mon": " ", "holiday": "휴진"}) is False


def test_public_payload_carries_the_holiday_value():
    hospital = _hospital_with_photo(None)
    hospital.business_hours = {**SEVEN_DAYS, "holiday": "휴진"}
    assert _serialize_hospital(hospital)["business_hours"]["holiday"] == "휴진"


async def test_saving_only_the_holiday_is_a_recorded_public_change(monkeypatch):
    """요일은 그대로, 공휴일만 바꾼 저장도 변경이다 — 출처 원장·감사·공개 페이지 재검증 대상."""
    import app.api.admin.hospitals as hospitals_api

    audits: list[dict] = []

    async def _capture_audit(_db, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(hospitals_api, "write_audit_log", _capture_audit)

    hospital = _active_complete_hospital(logo_url=None, business_hours=dict(SEVEN_DAYS))
    db = _FakeDB(hospital)
    await update_profile(
        hospital.id,
        HospitalProfileUpdate(business_hours={**SEVEN_DAYS, "holiday": "휴진"}),
        BackgroundTasks(),
        db=db,
    )

    assert db.committed is True
    assert hospital.business_hours == {**SEVEN_DAYS, "holiday": "휴진"}
    assert any("business_hours" in audit["detail"]["changed_fields"] for audit in audits)
    assert "business_hours" in hospitals_api.PUBLIC_PROFILE_FIELDS

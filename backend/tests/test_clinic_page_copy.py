"""병원마다 바꾸는 공개 페이지 문구 — 진료 원칙·주차·대중교통(2026-11-17, 0096)."""

import pytest
from pydantic import ValidationError

from app.api.admin.hospitals import HospitalProfileUpdate
from app.api.public.site import _serialize_hospital
from app.services.clinic_page_copy import (
    normalize_care_principles,
    public_care_principles,
)

from tests.test_public_site import _hospital_with_photo

SEOGWIPO = {
    "title": "아픈 원인부터 확인합니다",
    "lede": "어디가 왜 아픈지 먼저 살피고, 검사 결과와 치료 방법을 말씀드린 뒤 치료를 시작합니다.",
    "cards": [
        {
            "title": "검사 결과를 쉽게 풀어 드립니다",
            "body": "엑스레이·초음파 결과와 치료 기간을 어려운 용어 없이 설명드립니다.",
        },
        {
            "title": "토·일요일에도 진료합니다",
            "body": "토·일요일 오전 8시 30분부터 오후 1시까지 점심시간 없이 진료합니다.",
        },
        {
            "title": "치료 방법은 진찰 후 함께 정합니다",
            "body": "진찰 후 주사, 물리치료, 시술 등을 함께 결정합니다.",
        },
    ],
}


def test_marketing_request_copy_saves_as_written():
    profile = HospitalProfileUpdate(
        care_principles=SEOGWIPO,
        parking_note="건물 뒤편 무료 주차장 이용 가능(네이버 플레이스 기준)",
        transit_note="고래왓(남) 정류장 하차 후 도보 30m",
    )
    assert profile.care_principles == SEOGWIPO
    assert profile.parking_note == "건물 뒤편 무료 주차장 이용 가능(네이버 플레이스 기준)"
    assert profile.transit_note == "고래왓(남) 정류장 하차 후 도보 30m"


def test_blank_copy_clears_to_the_shared_default():
    assert normalize_care_principles({"title": " ", "lede": "", "cards": []}) is None
    assert HospitalProfileUpdate(parking_note="   ").parking_note is None


def test_cards_keep_their_slot_and_blank_slots_fall_back():
    saved = normalize_care_principles(
        {"cards": [None, {"title": "토·일요일에도 진료합니다", "body": "토·일요일에도 진료합니다."}]}
    )
    assert saved == {
        "title": None,
        "lede": None,
        "cards": [
            {"title": None, "body": None},
            {"title": "토·일요일에도 진료합니다", "body": "토·일요일에도 진료합니다."},
            {"title": None, "body": None},
        ],
    }


@pytest.mark.parametrize(
    "value",
    [
        {"title": "완치를 보장합니다"},
        {"cards": [{"title": "할인 이벤트", "body": "첫 방문 할인해 드립니다."}]},
        {"cards": [{"title": "제목만 있습니다"}]},
        {"cards": [{}, {}, {}, {}]},
        {"subtitle": "알 수 없는 칸"},
        {"title": "가" * 61},
    ],
)
def test_forbidden_or_malformed_principles_are_rejected(value):
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(care_principles=value)


def test_forbidden_visit_note_is_rejected():
    with pytest.raises(ValidationError):
        HospitalProfileUpdate(parking_note="주차 무료, 첫 방문 할인해 드립니다")


def test_public_payload_carries_the_copy_and_drops_forbidden_sentences():
    hospital = _hospital_with_photo(None)
    hospital.care_principles = SEOGWIPO
    hospital.parking_note = "건물 뒤편 주차장 이용 가능"
    hospital.transit_note = "고래왓(남) 정류장 하차 후 도보 30m"

    serialized = _serialize_hospital(hospital)

    assert serialized["care_principles"]["title"] == SEOGWIPO["title"]
    assert serialized["care_principles"]["cards"][1] == SEOGWIPO["cards"][1]
    assert serialized["parking_note"] == "건물 뒤편 주차장 이용 가능"
    assert serialized["transit_note"] == "고래왓(남) 정류장 하차 후 도보 30m"

    # 필터가 바뀌어 옛 저장값이 새로 걸려도 공개되지 않는다 — 그 문장만 비운다.
    stale = {**SEOGWIPO, "cards": [{"title": "완치 보장", "body": "100% 완치"}, *SEOGWIPO["cards"][1:]]}
    public = public_care_principles(stale)
    assert public["cards"][0] is None
    assert public["cards"][1] == SEOGWIPO["cards"][1]


def test_hospital_without_custom_copy_serializes_nulls():
    serialized = _serialize_hospital(_hospital_with_photo(None))
    assert serialized["care_principles"] is None
    assert serialized["parking_note"] is None
    assert serialized["transit_note"] is None


def test_director_philosophy_stays_private():
    """자유 입력 진료 철학은 공개하지 않는다 — 공개할 문장은 진료 원칙 칸에 쓴다."""
    hospital = _hospital_with_photo(None)
    hospital.director_philosophy = "꼭 필요한 치료만 정직하게 하겠습니다."
    assert _serialize_hospital(hospital)["director_philosophy"] is None


# ── 관리자 저장 경로(update_profile) ─────────────────────────────────────────


async def test_profile_patch_saves_page_copy_and_blank_clears_it():
    from fastapi import BackgroundTasks

    from app.api.admin.hospitals import serialize_hospital_detail, update_profile
    from tests.test_hospital_visual_identity import _active_complete_hospital, _FakeDB

    hospital = _active_complete_hospital(logo_url=None)
    db = _FakeDB(hospital)
    await update_profile(
        hospital.id,
        HospitalProfileUpdate(
            care_principles=SEOGWIPO,
            parking_note="건물 뒤편 주차장 이용 가능",
            transit_note="고래왓(남) 정류장 하차 후 도보 30m",
        ),
        BackgroundTasks(),
        db=db,
    )
    assert db.committed is True
    detail = serialize_hospital_detail(hospital)
    assert detail["care_principles"] == SEOGWIPO
    assert detail["parking_note"] == "건물 뒤편 주차장 이용 가능"
    assert _serialize_hospital(hospital)["transit_note"] == "고래왓(남) 정류장 하차 후 도보 30m"

    # 화면은 빈 칸을 빈 문자열로 보낸다 — 비우기는 공통 기본 문구로 돌아가는 것이다.
    db = _FakeDB(hospital)
    await update_profile(
        hospital.id,
        HospitalProfileUpdate(
            care_principles={"title": "", "lede": "", "cards": [{"title": "", "body": ""}] * 3},
            parking_note="",
            transit_note="",
        ),
        BackgroundTasks(),
        db=db,
    )
    assert db.committed is True
    assert hospital.care_principles is None
    assert hospital.parking_note is None
    assert hospital.transit_note is None

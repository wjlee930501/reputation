"""프로필 출처 원장(P4-4, D30·D34) — 마케터 입력은 값이 비어 있어도 마케터 소유다."""

import uuid
from types import SimpleNamespace

from fastapi import BackgroundTasks

from app.api.admin import hospitals as hospitals_api
from app.services import profile_provenance as prov
from app.services.audit_log import reset_request_actor, set_request_actor
from tests.test_admin_hospitals_profile import FakeDB, _hospital


def test_operator_fields_are_owned_even_when_empty() -> None:
    hospital = SimpleNamespace(profile_field_provenance={})
    prov.record_operator_fields(hospital, ["director_career"], actor="ae@example.com")
    assert prov.is_operator_owned(hospital, "director_career") is True
    assert prov.is_operator_owned(hospital, "address") is False
    assert hospital.profile_field_provenance["director_career"]["actor"] == "ae@example.com"


def test_system_actor_is_not_an_operator() -> None:
    hospital = SimpleNamespace(profile_field_provenance={})
    assert prov.record_operator_fields(hospital, ["address"], actor="system:backfill") == []
    assert hospital.profile_field_provenance == {}


def test_autofill_never_takes_over_an_operator_field() -> None:
    hospital = SimpleNamespace(profile_field_provenance={})
    prov.record_operator_fields(hospital, ["phone"], actor="ae")
    prov.record_autofill_fields(hospital, {"phone": "naver", "address": "naver"}, run_id="r1")
    assert prov.origin_of(hospital, "phone") == prov.ORIGIN_OPERATOR
    assert prov.origin_of(hospital, "address") == prov.ORIGIN_AUTOFILL
    assert hospital.profile_field_provenance["address"]["source"] == "naver"


def test_conflict_is_recorded_without_changing_origin() -> None:
    hospital = SimpleNamespace(profile_field_provenance={})
    prov.record_operator_fields(hospital, ["business_hours"], actor="ae")
    prov.record_conflict(
        hospital, "business_hours", source="naver", value={"mon": "10:00-19:00"}
    )
    entry = hospital.profile_field_provenance["business_hours"]
    assert entry["origin"] == prov.ORIGIN_OPERATOR
    assert entry["conflict"]["value"] == {"mon": "10:00-19:00"}
    # 마케터가 다시 고치면 충돌 기록은 새 저장으로 대체된다.
    prov.record_operator_fields(hospital, ["business_hours"], actor="ae")
    assert "conflict" not in hospital.profile_field_provenance["business_hours"]


def test_empty_values() -> None:
    assert prov.is_empty_value(None) and prov.is_empty_value("  ") and prov.is_empty_value([])
    assert prov.is_empty_value({"mon": None, "tue": " "})
    assert not prov.is_empty_value({"mon": "09:00-18:00"})
    assert not prov.is_empty_value(["치질"])


def test_semantic_change_ignores_resent_blank_keys() -> None:
    assert not prov.semantically_changed(
        {"mon": "09:00-18:00"}, {"mon": "09:00-18:00", "tue": None}
    )
    assert prov.semantically_changed({"mon": "09:00-18:00"}, {"mon": "10:00-18:00"})
    assert not prov.semantically_changed(None, "  ")


async def test_patch_records_only_changed_fields_as_operator() -> None:
    hospital = _hospital(profile_field_provenance={}, business_hours={"mon": "09:00-18:00"})
    db = FakeDB(hospital)
    token = set_request_actor("ae@example.com")
    try:
        result = await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(
                phone="02-111-2222",
                # 화면이 같은 진료시간을 빈 요일 키와 함께 다시 보낸다 — 바꾼 것이 아니다.
                business_hours={"mon": "09:00-18:00"},
                director_career=None,
            ),
            BackgroundTasks(),
            db=db,
        )
    finally:
        reset_request_actor(token)

    ledger = hospital.profile_field_provenance
    assert set(ledger) == {"phone", "director_career"}
    assert ledger["phone"]["origin"] == "OPERATOR"
    assert ledger["director_career"]["actor"] == "ae@example.com"
    assert result["profile_field_provenance"] == ledger


async def test_patch_by_a_system_actor_records_nothing() -> None:
    hospital = _hospital(profile_field_provenance={})
    db = FakeDB(hospital)
    token = set_request_actor("system:cli")
    try:
        await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(phone="02-111-2222"),
            BackgroundTasks(),
            db=db,
        )
    finally:
        reset_request_actor(token)
    assert hospital.profile_field_provenance == {}


async def test_patch_physicians_marks_derived_director_fields() -> None:
    hospital = _hospital(profile_field_provenance={}, director_name=None, director_career=None)
    db = FakeDB(hospital)
    await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(
            physicians=[
                {"name": "박원장", "career": "내과 전문의", "is_representative": True}
            ]
        ),
        BackgroundTasks(),
        db=db,
    )
    ledger = hospital.profile_field_provenance
    assert ledger["director_name"]["origin"] == "OPERATOR"
    assert ledger["director_career"]["origin"] == "OPERATOR"
    assert "physicians" not in ledger


def test_serializer_exposes_the_ledger_and_a_missing_column_is_empty() -> None:
    hospital = _hospital(profile_field_provenance={"phone": {"origin": "OPERATOR"}})
    assert hospitals_api.serialize_hospital_detail(hospital)["profile_field_provenance"] == {
        "phone": {"origin": "OPERATOR"}
    }
    legacy = _hospital()
    assert hospitals_api.serialize_hospital_detail(legacy)["profile_field_provenance"] == {}
    assert uuid.UUID(str(legacy.id))

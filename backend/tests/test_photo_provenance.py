"""공개 사진 권리 근거 정책의 순수 함수 — 0052 CHECK와 같은 판단인지 고정한다."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.photo_provenance import (
    PHOTO_RIGHTS_BASES,
    InvalidPhotoRightsBasis,
    apply_photo_provenance,
    describe_missing_provenance,
    missing_photo_provenance,
    missing_provenance_input_fields,
    normalize_provenance_input,
    photo_provenance_is_complete,
    serialize_photo_provenance,
)


def _photo(**overrides):
    row = SimpleNamespace(
        photo_source_owner=None,
        photo_rights_basis=None,
        photo_evidence_reference=None,
        photo_verified_by=None,
        photo_verified_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _verified_photo():
    return _photo(
        photo_source_owner="장편한외과의원",
        photo_rights_basis="OWNER_CONSENT",
        photo_evidence_reference="촬영 동의서 2026-04",
        photo_verified_by="ae@motionlabs.io",
        photo_verified_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )


def test_a_row_with_every_value_is_publishable():
    assert missing_photo_provenance(_verified_photo()) == []
    assert photo_provenance_is_complete(_verified_photo()) is True


@pytest.mark.parametrize(
    "field",
    [
        "photo_source_owner",
        "photo_rights_basis",
        "photo_evidence_reference",
        "photo_verified_by",
        "photo_verified_at",
    ],
)
def test_every_field_the_constraint_requires_is_checked(field: str):
    row = _verified_photo()
    setattr(row, field, None)

    assert missing_photo_provenance(row) == [field]


def test_blank_strings_do_not_pass_as_evidence():
    row = _verified_photo()
    row.photo_source_owner = "   "
    row.photo_evidence_reference = ""

    assert missing_photo_provenance(row) == [
        "photo_source_owner",
        "photo_evidence_reference",
    ]


def test_a_new_upload_can_be_judged_before_the_row_exists():
    """저장 전에 판단할 수 있어야 근거 없는 공개 업로드를 파일 저장 전에 돌려보낸다."""
    complete = normalize_provenance_input("병원", "LICENSE", "계약서 5조")
    partial = normalize_provenance_input("병원", None, None)

    assert missing_provenance_input_fields(complete) == []
    assert missing_provenance_input_fields(partial) == [
        "photo_rights_basis",
        "photo_evidence_reference",
    ]
    # 확인자·확인 시각은 서버가 찍으므로 운영자에게 요구하지 않는다.
    assert "photo_verified_by" not in missing_provenance_input_fields(partial)


def test_a_rights_basis_the_database_cannot_store_is_rejected_early():
    with pytest.raises(InvalidPhotoRightsBasis):
        normalize_provenance_input(rights_basis="아마 괜찮음")


@pytest.mark.parametrize("basis", PHOTO_RIGHTS_BASES)
def test_rights_basis_is_accepted_case_insensitively(basis: str):
    normalized = normalize_provenance_input(rights_basis=basis.lower())

    assert normalized.rights_basis == basis


def test_the_server_stamps_who_verified_and_when():
    row = _photo()
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)

    recorded = apply_photo_provenance(
        row,
        normalize_provenance_input("병원", "license", "계약서 5조"),
        verified_by="ae@motionlabs.io",
        now=now,
    )

    assert recorded is True
    assert row.photo_rights_basis == "LICENSE"
    assert row.photo_verified_by == "ae@motionlabs.io"
    assert row.photo_verified_at == now
    assert photo_provenance_is_complete(row)


def test_an_empty_request_does_not_rewrite_an_existing_verification():
    row = _verified_photo()
    original_verified_at = row.photo_verified_at

    recorded = apply_photo_provenance(
        row,
        normalize_provenance_input(),
        verified_by="other@motionlabs.io",
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert recorded is False
    assert row.photo_verified_by == "ae@motionlabs.io"
    assert row.photo_verified_at == original_verified_at


def test_a_partial_correction_keeps_the_untouched_values():
    row = _verified_photo()

    apply_photo_provenance(
        row,
        normalize_provenance_input(source_owner="새 소유자"),
        verified_by="ae2@motionlabs.io",
    )

    assert row.photo_source_owner == "새 소유자"
    assert row.photo_evidence_reference == "촬영 동의서 2026-04"
    # 값이 하나라도 바뀌었으면 누가 언제 확인했는지도 갱신된다.
    assert row.photo_verified_by == "ae2@motionlabs.io"


def test_the_operator_is_told_what_to_fill_in():
    message = describe_missing_provenance(
        ["photo_source_owner", "photo_rights_basis", "photo_verified_at"]
    )

    assert "사진 소유자" in message
    assert "권리 근거" in message
    # 서버가 찍는 값은 운영자가 채울 수 없으니 요구 목록에 나오지 않는다.
    assert "확인 시각" not in message


def test_serialization_tells_the_admin_screen_what_is_missing():
    payload = serialize_photo_provenance(_photo(photo_source_owner="병원"))

    assert payload["is_complete"] is False
    assert payload["source_owner"] == "병원"
    assert "photo_rights_basis" in payload["missing_fields"]
    assert payload["missing_message"]


def test_serialization_of_a_verified_photo_has_nothing_left_to_do():
    payload = serialize_photo_provenance(_verified_photo())

    assert payload["is_complete"] is True
    assert payload["missing_fields"] == []
    assert payload["missing_message"] is None
    assert payload["rights_basis_label"] == "촬영 대상·소유자 동의"

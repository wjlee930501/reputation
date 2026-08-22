from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin.essence import (
    build_photo_source_metadata,
    resolve_patched_photo_metadata,
    resolve_upload_is_public,
    should_revalidate_after_public_photo_upload,
    validate_photo_source_metadata,
)
from app.models.essence import PHOTO_SOURCE_TYPES, SourceType
from app.models.hospital import HospitalStatus
from app.services.photo_assets import effective_photo_metadata, legacy_photo_asset_kind

FACILITY_PHOTO_TYPES = [
    source_type for source_type in PHOTO_SOURCE_TYPES if source_type != SourceType.PHOTO_DOCTOR
]


@pytest.mark.parametrize("source_type", PHOTO_SOURCE_TYPES)
def test_photo_upload_can_be_public(source_type: SourceType):
    assert resolve_upload_is_public(source_type, True) is True


@pytest.mark.parametrize("source_type", PHOTO_SOURCE_TYPES)
def test_photo_upload_defaults_to_public(source_type: SourceType):
    assert resolve_upload_is_public(source_type, None) is True


@pytest.mark.parametrize("source_type", PHOTO_SOURCE_TYPES)
def test_photo_upload_can_stay_private(source_type: SourceType):
    assert resolve_upload_is_public(source_type, False) is False


@pytest.mark.parametrize(
    "source_type",
    [source_type for source_type in SourceType if source_type not in PHOTO_SOURCE_TYPES],
)
def test_non_photo_upload_ignores_public_request(source_type: SourceType):
    assert resolve_upload_is_public(source_type, True) is False


def test_public_photo_upload_revalidates_live_site():
    hospital = SimpleNamespace(status=HospitalStatus.ACTIVE, site_live=True)
    assert should_revalidate_after_public_photo_upload(
        SourceType.PHOTO_DOCTOR, True, hospital
    ) is True


def test_private_or_non_photo_upload_skips_revalidate():
    hospital = SimpleNamespace(status=HospitalStatus.ACTIVE, site_live=True)
    assert should_revalidate_after_public_photo_upload(
        SourceType.PHOTO_DOCTOR, False, hospital
    ) is False
    assert should_revalidate_after_public_photo_upload(
        SourceType.HOMEPAGE, True, hospital
    ) is False


def test_photo_upload_skips_revalidate_when_site_is_not_live():
    hospital = SimpleNamespace(status=HospitalStatus.ACTIVE, site_live=False)
    assert should_revalidate_after_public_photo_upload(
        SourceType.PHOTO_CLINIC_EXTERIOR, True, hospital
    ) is False


def test_doctor_photo_metadata_requires_an_explicit_identity_or_editorial_role():
    verified = build_photo_source_metadata(
        SourceType.PHOTO_DOCTOR,
        "VERIFIED_REAL_PERSON",
        "doctor.jpg",
    )
    assert verified["approved_usage"] == ["DOCTOR_IDENTITY"]

    with pytest.raises(HTTPException):
        build_photo_source_metadata(SourceType.PHOTO_DOCTOR, None, "doctor.jpg")


def test_facility_photo_metadata_cannot_claim_a_person_identity_role():
    metadata = build_photo_source_metadata(
        SourceType.PHOTO_CLINIC_INTERIOR,
        "VERIFIED_FACILITY",
        "clinic.jpg",
    )
    assert metadata["approved_usage"] == ["HERO", "GALLERY"]


def test_photo_metadata_derives_usage_instead_of_trusting_the_client():
    metadata = validate_photo_source_metadata(
        SourceType.PHOTO_DOCTOR,
        {
            "original_filename": "director.jpg",
            "asset_kind": "EDITORIAL_GRAPHIC",
            "approved_usage": ["DOCTOR_IDENTITY", "HERO"],
            "migration_note": "legacy review",
        },
    )

    assert metadata["approved_usage"] == ["CONTENT_EDITORIAL"]
    assert metadata["migration_note"] == "legacy review"


def test_photo_metadata_rejects_kind_that_does_not_match_source_type():
    with pytest.raises(HTTPException):
        validate_photo_source_metadata(
            SourceType.PHOTO_CLINIC_INTERIOR,
            {"asset_kind": "VERIFIED_REAL_PERSON"},
        )


def test_unclassified_upload_still_fails_so_the_operator_picks_a_role():
    with pytest.raises(HTTPException):
        validate_photo_source_metadata(SourceType.PHOTO_DOCTOR, {})
    with pytest.raises(HTTPException):
        validate_photo_source_metadata(SourceType.PHOTO_CLINIC_EXTERIOR, {})


@pytest.mark.parametrize("source_type", PHOTO_SOURCE_TYPES)
def test_legacy_photo_recovers_instead_of_blocking_republish(source_type: SourceType):
    recovered = validate_photo_source_metadata(
        source_type,
        {"original_filename": "legacy.jpg"},
        allow_legacy_recovery=True,
    )

    assert recovered["asset_kind"] == legacy_photo_asset_kind(source_type)
    assert recovered["approved_usage"]
    assert recovered["asset_kind_source"] == "LEGACY_BACKFILL"
    assert recovered["needs_operator_review"] is True
    assert recovered["original_filename"] == "legacy.jpg"


def test_legacy_recovery_never_promotes_a_doctor_photo_to_identity():
    recovered = validate_photo_source_metadata(
        SourceType.PHOTO_DOCTOR,
        {},
        allow_legacy_recovery=True,
    )

    assert recovered["asset_kind"] == "EDITORIAL_GRAPHIC"
    assert "DOCTOR_IDENTITY" not in recovered["approved_usage"]


@pytest.mark.parametrize("source_type", FACILITY_PHOTO_TYPES)
def test_legacy_recovery_keeps_facility_photos_on_the_public_surface(source_type: SourceType):
    recovered = validate_photo_source_metadata(source_type, {}, allow_legacy_recovery=True)

    assert recovered["asset_kind"] == "VERIFIED_FACILITY"
    assert recovered["approved_usage"] == ["HERO", "GALLERY"]


def test_legacy_recovery_tolerates_metadata_that_is_not_a_mapping():
    recovered = validate_photo_source_metadata(
        SourceType.PHOTO_CLINIC_INTERIOR,
        None,
        allow_legacy_recovery=True,
    )

    assert recovered["asset_kind"] == "VERIFIED_FACILITY"


def test_patch_without_a_kind_keeps_the_stored_classification():
    resolved = resolve_patched_photo_metadata(
        SourceType.PHOTO_DOCTOR,
        {"asset_kind": "VERIFIED_REAL_PERSON", "original_filename": "director.jpg"},
        {"alt_text": "원장 프로필"},
    )

    assert resolved["asset_kind"] == "VERIFIED_REAL_PERSON"
    assert resolved["approved_usage"] == ["DOCTOR_IDENTITY"]
    assert resolved["alt_text"] == "원장 프로필"
    assert "asset_kind_source" not in resolved


def test_patch_on_a_legacy_row_recovers_rather_than_rejecting():
    resolved = resolve_patched_photo_metadata(
        SourceType.PHOTO_DOCTOR,
        {"original_filename": "legacy.jpg"},
        {"operator_hint": "확인 필요"},
    )

    assert resolved["asset_kind"] == "EDITORIAL_GRAPHIC"
    assert resolved["asset_kind_source"] == "LEGACY_BACKFILL"
    assert resolved["operator_hint"] == "확인 필요"


def test_operator_classification_clears_the_legacy_review_flag():
    resolved = resolve_patched_photo_metadata(
        SourceType.PHOTO_DOCTOR,
        {
            "asset_kind": "EDITORIAL_GRAPHIC",
            "asset_kind_source": "LEGACY_BACKFILL",
            "needs_operator_review": True,
        },
        {"asset_kind": "VERIFIED_REAL_PERSON"},
    )

    assert resolved["asset_kind"] == "VERIFIED_REAL_PERSON"
    assert resolved["approved_usage"] == ["DOCTOR_IDENTITY"]
    assert "asset_kind_source" not in resolved
    assert "needs_operator_review" not in resolved


def test_public_read_fills_legacy_gaps_without_changing_what_renders():
    doctor = effective_photo_metadata(SourceType.PHOTO_DOCTOR, {})
    facility = effective_photo_metadata(SourceType.PHOTO_CLINIC_EXTERIOR, None)

    assert doctor["asset_kind"] == "EDITORIAL_GRAPHIC"
    assert facility["approved_usage"] == ["HERO", "GALLERY"]


def test_public_read_leaves_an_operator_classification_untouched():
    stored = {
        "asset_kind": "VERIFIED_REAL_PERSON",
        "approved_usage": ["DOCTOR_IDENTITY"],
        "original_filename": "director.jpg",
    }

    assert effective_photo_metadata(SourceType.PHOTO_DOCTOR, stored) == stored

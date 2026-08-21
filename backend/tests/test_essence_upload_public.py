from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin.essence import (
    build_photo_source_metadata,
    require_verified_photo_provenance,
    resolve_upload_is_public,
    should_revalidate_after_public_photo_upload,
    validate_photo_source_metadata,
)
from app.models.essence import PHOTO_SOURCE_TYPES, SourceType
from app.models.hospital import HospitalStatus


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


def test_brand_graphic_metadata_has_explicit_logo_and_hero_usage_only():
    metadata = build_photo_source_metadata(
        SourceType.PHOTO_BRAND,
        "VERIFIED_BRAND_GRAPHIC",
        "logo.png",
    )

    assert metadata["approved_usage"] == ["LOGO", "HERO"]


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


def test_named_doctor_publication_requires_positive_server_verification():
    # Given: a doctor classification whose operator provenance was never verified.
    source = SimpleNamespace(
        source_type=SourceType.PHOTO_DOCTOR,
        source_metadata={"asset_kind": "VERIFIED_REAL_PERSON"},
        photo_source_owner="장 원장",
        photo_rights_basis="OWNER_CONSENT",
        photo_evidence_reference="consent/2026-08-22",
        photo_verified_by=None,
        photo_verified_at=None,
    )

    # When / Then: it cannot cross the public boundary based on classification alone.
    with pytest.raises(HTTPException, match="verification"):
        require_verified_photo_provenance(source)


def test_named_facility_publication_accepts_complete_verified_provenance():
    # Given: operator-provided rights evidence plus server-recorded verifier identity/time.
    source = SimpleNamespace(
        source_type=SourceType.PHOTO_CLINIC_EXTERIOR,
        source_metadata={"asset_kind": "VERIFIED_FACILITY"},
        photo_source_owner="장편한외과의원",
        photo_rights_basis="LICENSE",
        photo_evidence_reference="contract/asset-42",
        photo_verified_by="owner@example.com",
        photo_verified_at=object(),
    )

    # When / Then: the positive verification gate accepts it.
    require_verified_photo_provenance(source)

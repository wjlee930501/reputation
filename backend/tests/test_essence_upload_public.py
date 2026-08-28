from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin.essence import (
    build_photo_source_metadata,
    is_photo_classification_only,
    photo_file_is_the_material,
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
def test_complete_photo_upload_never_needs_a_followup_publish(source_type: SourceType):
    assert resolve_upload_is_public(source_type, False, provenance_complete=True) is True


@pytest.mark.parametrize("source_type", PHOTO_SOURCE_TYPES)
def test_photo_upload_without_complete_provenance_stays_private(source_type: SourceType):
    assert resolve_upload_is_public(source_type, None, provenance_complete=False) is False
    assert resolve_upload_is_public(source_type, False, provenance_complete=False) is False


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


def test_classifying_a_photo_keeps_what_was_already_stored_about_it():
    """Choosing a role is not a reason to forget the upload filename or operator notes."""
    resolved = resolve_patched_photo_metadata(
        SourceType.PHOTO_DOCTOR,
        {
            "original_filename": "director-2026.jpg",
            "alt_text": "진료 중인 원장",
            "asset_kind": "EDITORIAL_GRAPHIC",
            "asset_kind_source": "LEGACY_BACKFILL",
            "needs_operator_review": True,
        },
        {"asset_kind": "VERIFIED_REAL_PERSON"},
    )

    assert resolved["original_filename"] == "director-2026.jpg"
    assert resolved["alt_text"] == "진료 중인 원장"
    assert resolved["asset_kind"] == "VERIFIED_REAL_PERSON"


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


def test_a_stored_kind_that_no_longer_fits_its_photo_type_recovers_instead_of_locking():
    """A row whose source type changed after classification must not be stuck at 422.

    Re-publishing and re-classifying both read the stored kind back. If the stored
    kind is impossible for the current type, refusing it leaves the operator with no
    way out, so the row falls back to the conservative recovery role.
    """
    stuck = {"original_filename": "clinic.jpg", "asset_kind": "VERIFIED_REAL_PERSON"}

    republished = validate_photo_source_metadata(
        SourceType.PHOTO_CLINIC_INTERIOR, stuck, allow_legacy_recovery=True
    )
    assert republished["asset_kind"] == "VERIFIED_FACILITY"
    assert republished["needs_operator_review"] is True

    patched = resolve_patched_photo_metadata(
        SourceType.PHOTO_CLINIC_INTERIOR, stuck, {"alt_text": "진료실"}
    )
    assert patched["asset_kind"] == "VERIFIED_FACILITY"
    assert patched["alt_text"] == "진료실"


def test_recovering_a_mismatched_kind_never_promotes_a_doctor_photo_to_identity():
    recovered = validate_photo_source_metadata(
        SourceType.PHOTO_DOCTOR,
        {"asset_kind": "VERIFIED_FACILITY"},
        allow_legacy_recovery=True,
    )

    assert recovered["asset_kind"] == "EDITORIAL_GRAPHIC"
    assert "DOCTOR_IDENTITY" not in recovered["approved_usage"]


def test_a_client_supplied_kind_is_still_refused_when_it_does_not_fit_the_type():
    """Recovery is only for stored rows. An explicit write keeps failing."""
    with pytest.raises(HTTPException):
        validate_photo_source_metadata(
            SourceType.PHOTO_CLINIC_INTERIOR, {"asset_kind": "VERIFIED_REAL_PERSON"}
        )
    with pytest.raises(HTTPException):
        resolve_patched_photo_metadata(
            SourceType.PHOTO_CLINIC_INTERIOR,
            {"asset_kind": "VERIFIED_FACILITY"},
            {"asset_kind": "VERIFIED_REAL_PERSON"},
        )


@pytest.mark.parametrize("source_type", PHOTO_SOURCE_TYPES)
def test_an_uploaded_photo_needs_no_url_or_body_text_to_be_classified(source_type: SourceType):
    """Photo uploads store only a file, so requiring text material blocked every
    classification PATCH with a 400 — including the rows recovery flags for review."""
    assert photo_file_is_the_material(source_type, "gs://bucket/photo.jpg") is True
    assert photo_file_is_the_material(source_type, None) is False


def test_text_materials_still_require_a_url_or_a_body():
    assert photo_file_is_the_material(SourceType.HOMEPAGE, "gs://bucket/report.pdf") is False
    assert photo_file_is_the_material(SourceType.BROCHURE, None) is False


def test_classifying_a_photo_is_not_a_material_change_that_resets_processing():
    assert is_photo_classification_only(SourceType.PHOTO_DOCTOR, {"source_metadata"}) is True
    assert (
        is_photo_classification_only(SourceType.PHOTO_DOCTOR, {"source_metadata", "updated_by"})
        is True
    )
    # D-1: 사진 제목은 공개 표면의 사진 설명일 뿐이다. 일괄 업로드로 같아진 설명을
    # 고치는 일이 처리 상태를 되돌리거나 운영 기준 스냅샷을 낡게 만들어서는 안 된다.
    assert is_photo_classification_only(SourceType.PHOTO_DOCTOR, {"title"}) is True
    assert (
        is_photo_classification_only(SourceType.PHOTO_DOCTOR, {"source_metadata", "title"}) is True
    )
    # Editing the material itself still reprocesses, and text sources are unaffected.
    assert is_photo_classification_only(SourceType.PHOTO_DOCTOR, {"raw_text"}) is False
    assert is_photo_classification_only(SourceType.PHOTO_DOCTOR, {"title", "url"}) is False
    assert is_photo_classification_only(SourceType.HOMEPAGE, {"source_metadata"}) is False
    # 텍스트 자료의 제목은 content_hash와 스냅샷에 들어가므로 여전히 재처리 대상이다.
    assert is_photo_classification_only(SourceType.HOMEPAGE, {"title"}) is False


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

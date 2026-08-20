from types import SimpleNamespace

import pytest

from app.api.admin.essence import (
    resolve_upload_is_public,
    should_revalidate_after_public_photo_upload,
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
    "source_type", [source_type for source_type in SourceType if source_type not in PHOTO_SOURCE_TYPES]
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
    resolve_upload_is_public,
    should_revalidate_after_public_photo_upload,
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
    "source_type", [source_type for source_type in SourceType if source_type not in PHOTO_SOURCE_TYPES]
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

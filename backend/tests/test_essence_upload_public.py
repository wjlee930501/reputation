import pytest

from app.api.admin.essence import resolve_upload_is_public
from app.models.essence import PHOTO_SOURCE_TYPES, SourceType


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

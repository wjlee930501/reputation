"""Persisted legacy reports and new value reports share delivery validation safely."""

import pytest

from app.schemas.report import DoctorArtifactProjection
from app.services.report_artifact_validation import parse_doctor_artifact_metadata


def _metadata(version: str, pages: int) -> dict:
    return {
        "validation_version": version,
        "validation_source": "SYSTEM",
        "page_count": pages,
        "page_size": "A4",
        "glyph_count": 840,
        "font_family": "Pretendard",
        "font_embedded": True,
        "korean_to_unicode": True,
        "link_count": 1,
        "expected_link_present": True,
        "required_text_present": True,
        "sha256": "a" * 64,
        "byte_size": 4096,
    }


@pytest.mark.parametrize("version,pages", [
    ("doctor-pdf-v1", 1), ("doctor-pdf-v1", 2),
    ("doctor-pdf-v2", 1), ("doctor-pdf-v2", 7),
    ("doctor-pdf-v3", 4), ("doctor-pdf-v3", 32),
])
def test_legacy_and_v3_metadata_survive_admin_serialization(version, pages):
    parsed = parse_doctor_artifact_metadata(_metadata(version, pages))
    assert parsed is not None
    projection = DoctorArtifactProjection(
        state="VALID", state_label="원장 전달용 PDF 검증 완료",
        validation_version=parsed.validation_version, page_count=parsed.page_count,
        sha256=parsed.sha256, byte_size=parsed.byte_size,
    )
    assert projection.model_dump()["validation_version"] == version
    assert projection.page_count == pages


@pytest.mark.parametrize("version,pages", [
    ("doctor-pdf-v1", 3), ("doctor-pdf-v2", 33),
    ("doctor-pdf-v3", 1), ("doctor-pdf-v3", 3), ("doctor-pdf-v3", 33),
])
def test_version_page_boundaries_remain_fail_closed(version, pages):
    assert parse_doctor_artifact_metadata(_metadata(version, pages)) is None


@pytest.mark.parametrize("field", ["sha256", "font_embedded", "required_text_present"])
def test_new_version_cannot_bypass_existing_artifact_proof(field):
    value = _metadata("doctor-pdf-v3", 4)
    value[field] = "invalid" if field == "sha256" else False
    assert parse_doctor_artifact_metadata(value) is None

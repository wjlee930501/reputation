from types import SimpleNamespace

from app.api.admin.essence import (
    _filename_without_extension,
    resolve_upload_title,
    should_revalidate_after_public_photo_upload,
    should_revalidate_on_source_upload,
)
from app.models.essence import SourceType
from app.models.hospital import HospitalStatus


class TestFilenameWithoutExtension:
    def test_simple_filename(self):
        assert _filename_without_extension("photo.jpg") == "photo"

    def test_multiple_dots(self):
        assert _filename_without_extension("clinic.exterior.2024.jpg") == "clinic.exterior.2024"

    def test_no_extension(self):
        assert _filename_without_extension("photo") == "photo"

    def test_empty_string(self):
        assert _filename_without_extension("") == ""

    def test_basename_only_forward_slash(self):
        assert _filename_without_extension("/path/to/photo.jpg") == "photo"
        assert _filename_without_extension("path/to/photo.jpg") == "photo"

    def test_basename_only_backslash(self):
        assert _filename_without_extension("C:\\Users\\doctor\\photo.jpg") == "photo"
        assert _filename_without_extension("folder\\photo.jpg") == "photo"

    def test_mixed_path_separators(self):
        assert _filename_without_extension("C:\\Users/doctor\\clinic/photo.jpg") == "photo"

    def test_truncate_to_300_chars(self):
        long_name = "a" * 350 + ".jpg"
        result = _filename_without_extension(long_name)
        assert len(result) == 300
        assert result == "a" * 300

    def test_truncate_basename_with_path(self):
        long_name = "x" * 350
        result = _filename_without_extension(f"/path/to/{long_name}.jpg")
        assert len(result) == 300
        assert result == "x" * 300

    def test_korean_filename(self):
        assert _filename_without_extension("병원외관.jpg") == "병원외관"
        assert _filename_without_extension("/사진/병원_내부.png") == "병원_내부"

    def test_special_characters(self):
        assert _filename_without_extension("clinic-photo_2024 (1).jpg") == "clinic-photo_2024 (1)"


class TestResolveUploadTitle:
    def test_explicit_title_wins(self):
        assert resolve_upload_title("원장 프로필", "ignored.jpg") == "원장 프로필"

    def test_empty_title_uses_filename(self):
        assert resolve_upload_title("", "clinic.jpg") == "clinic"

    def test_blank_title_uses_filename(self):
        assert resolve_upload_title("   ", "clinic.jpg") == "clinic"

    def test_empty_title_and_empty_filename(self):
        assert resolve_upload_title("", "") == "업로드 파일"

    def test_title_is_truncated_to_300(self):
        assert len(resolve_upload_title("한" * 400, "x.jpg")) == 300


class TestSkipRevalidateContract:
    def _live(self):
        return SimpleNamespace(status=HospitalStatus.ACTIVE, site_live=True)

    def test_skip_false_public_photo_revalidates(self):
        assert should_revalidate_on_source_upload(
            False, SourceType.PHOTO_DOCTOR, True, self._live()
        ) is True

    def test_skip_true_suppresses_revalidate(self):
        assert should_revalidate_on_source_upload(
            True, SourceType.PHOTO_DOCTOR, True, self._live()
        ) is False

    def test_private_photo_never_revalidates(self):
        assert should_revalidate_after_public_photo_upload(
            SourceType.PHOTO_DOCTOR, False, self._live()
        ) is False

    def test_batch_pattern_all_skip_then_explicit_once(self):
        live = self._live()
        skipped = [
            should_revalidate_on_source_upload(True, SourceType.PHOTO_CLINIC_EXTERIOR, True, live)
            for _ in range(3)
        ]
        assert skipped == [False, False, False]

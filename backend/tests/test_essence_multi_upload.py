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

    def test_a_batch_keeps_one_distinct_title_per_file(self):
        """D-1 — 제목 칸이 하나였을 때 N개 자산이 같은 제목으로 저장되고, 그 제목이
        공개 표면의 사진 설명이 되어 갤러리가 같은 문구를 반복했다. 화면은 이제 파일별
        제목을 보내므로 서로 다른 제목이 그대로 저장되어야 한다."""
        batch = [("외관", "exterior.jpg"), ("진료실", "room.jpg"), ("대기실", "lobby.png")]
        titles = [resolve_upload_title(title, filename) for title, filename in batch]

        assert titles == ["외관", "진료실", "대기실"]
        assert len(set(titles)) == len(titles)

    def test_a_batch_with_no_titles_falls_back_to_distinct_filenames(self):
        titles = [
            resolve_upload_title("", filename)
            for filename in ("exterior.jpg", "room.jpg", "lobby.png")
        ]

        assert titles == ["exterior", "room", "lobby"]
        assert len(set(titles)) == len(titles)


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

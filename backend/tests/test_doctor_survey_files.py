"""원장 사전 설문 파일 — 종류 판별·한도·답의 파일 id 검사(순수 함수)."""

from __future__ import annotations

import pytest

from app.services import doctor_survey_files as files

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.mark.parametrize(
    ("data", "mime"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (PDF, "application/pdf"), (HEIC, "image/heic"), (SVG, None)],
)
def test_type_is_sniffed_from_bytes(data, mime):
    assert files.sniff_type(data) == mime


def test_kinds_accept_only_their_types():
    assert files.check_upload("PHOTO", JPEG, 0) == "image/jpeg"
    assert files.check_upload("CERTIFICATE", PDF, 0) == "application/pdf"
    for kind, data in (("PHOTO", PDF), ("LOGO", SVG), ("PHOTO", b"")):
        with pytest.raises(files.SurveyFileError):
            files.check_upload(kind, data, 0)
    with pytest.raises(files.SurveyFileError):
        files.check_upload("BACKDOOR", PNG, 0)
    with pytest.raises(files.SurveyFileError):
        files.check_upload("PHOTO", PNG, files.MAX_FILES_PER_SURVEY)
    with pytest.raises(files.SurveyFileError) as too_big:
        files.check_upload("PHOTO", b"\xff\xd8\xff" + b"0" * files.MAX_FILE_BYTES, 0)
    assert too_big.value.status_code == 413


def test_answers_cannot_point_at_files_this_survey_never_received():
    known = {"a" * 32: {"kind": "PHOTO"}, "c" * 32: {"kind": "CERTIFICATE"}}
    answers = {
        "photos": [{"id": "a" * 32, "name": "1.jpg"}, {"id": "b" * 32, "name": "남의 파일"}],
        "logo_files": [{"id": "d" * 32, "name": "x"}],
        "certifications": [{"name": "의료기관 인증", "files": [{"id": "c" * 32, "name": "인증.pdf"}, {"id": "e" * 32}]}],
    }
    cleaned = files.drop_unknown_files(answers, known)
    assert cleaned["photos"] == [{"id": "a" * 32, "name": "1.jpg"}]
    assert "logo_files" not in cleaned
    assert cleaned["certifications"][0]["files"] == [{"id": "c" * 32, "name": "인증.pdf"}]


def test_referenced_files_lists_what_the_answers_still_point_at():
    stored = {
        "a" * 32: {"kind": "PHOTO", "name": "외관.jpg", "mime": "image/jpeg", "size": 10},
        "z" * 32: {"kind": "PHOTO", "name": "지운 사진.jpg", "mime": "image/jpeg", "size": 10},
        "c" * 32: {"kind": "CERTIFICATE", "name": "인증.pdf", "mime": "application/pdf", "size": 10},
    }
    answers = {
        "photos": [{"id": "a" * 32}],
        "certifications": [{"name": "의료기관 인증", "files": [{"id": "c" * 32}]}],
    }
    listed = files.referenced_files(answers, stored)
    assert [(item["name"], item["context"]) for item in listed] == [("외관.jpg", None), ("인증.pdf", "의료기관 인증")]

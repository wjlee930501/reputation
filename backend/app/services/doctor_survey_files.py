"""원장 사전 설문에 올린 파일(사진·로고·인증서).

로그인 없는 공개 링크에서 받는 파일이라 보수적으로 받는다.

- 종류는 **파일 첫 바이트**로 판별한다(PNG·JPEG·WebP·GIF·HEIC 사진, 인증서는 PDF도). 확장자·MIME은 믿지 않는다 —
  SVG·HTML처럼 브라우저에서 실행될 수 있는 파일은 받지 않는다(로고 AI·SVG 파일은 담당자에게 따로).
- 한 파일 12MB, 설문 하나에 60개까지.
- 저장은 병원별 비공개 저장소(`asset_storage.store_asset_bytes`)다. 공개 사진 자료가 아니다 — 사진 자료로
  등록(분류·권리 근거·공개)은 AE가 병원 정보 탭에서 한다. 원장이 '사진 사용 동의'를 켰는지는 함께 보인다.
- 답(`answers`)에는 파일 id만 들어간다. 저장할 때 이 설문에 올린 적 없는 id는 버린다.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services import doctor_survey_schema as schema
from app.services.survey_sources import mask_personal_info

MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_FILES_PER_SURVEY = 60
KIND_PHOTO = "PHOTO"
KIND_LOGO = "LOGO"
KIND_CERTIFICATE = "CERTIFICATE"
KINDS = (KIND_PHOTO, KIND_LOGO, KIND_CERTIFICATE)

_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/heic")
_ALLOWED_TYPES = {
    KIND_PHOTO: _IMAGE_TYPES,
    KIND_LOGO: _IMAGE_TYPES,
    KIND_CERTIFICATE: (*_IMAGE_TYPES, "application/pdf"),
}
_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
    "application/pdf": "pdf",
}
TYPE_MESSAGES = {
    KIND_PHOTO: "사진은 JPG·PNG·HEIC 파일만 올릴 수 있습니다.",
    KIND_LOGO: "로고는 PNG·JPG 파일만 올릴 수 있습니다. AI·SVG 파일은 담당자에게 따로 보내 주세요.",
    KIND_CERTIFICATE: "지정서·인증서는 사진(JPG·PNG) 또는 PDF 파일만 올릴 수 있습니다.",
}


class SurveyFileError(ValueError):
    """받지 않는 파일. 메시지는 화면에 그대로 보여 줄 수 있다."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def sniff_type(data: bytes) -> str | None:
    """파일 첫 바이트로 종류를 판별한다. 모르면 None."""

    head = data[:32]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[4:8] == b"ftyp" and head[8:12] in {b"heic", b"heix", b"mif1", b"msf1", b"hevc"}:
        return "image/heic"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def check_upload(kind: str, data: bytes, existing_count: int) -> str:
    """받을 수 있으면 판별한 MIME을, 아니면 :class:`SurveyFileError`."""

    if kind not in KINDS:
        raise SurveyFileError("올릴 파일 종류가 맞지 않습니다. 화면을 새로고침해 주세요.")
    if existing_count >= MAX_FILES_PER_SURVEY:
        raise SurveyFileError(f"파일은 {MAX_FILES_PER_SURVEY}개까지 올릴 수 있습니다. 나머지는 담당자에게 보내 주세요.")
    if not data:
        raise SurveyFileError("빈 파일입니다.")
    if len(data) > MAX_FILE_BYTES:
        raise SurveyFileError("파일이 너무 큽니다. 한 파일에 12MB까지 올릴 수 있습니다.", status_code=413)
    mime = sniff_type(data)
    if mime is None or mime not in _ALLOWED_TYPES[kind]:
        raise SurveyFileError(TYPE_MESSAGES[kind])
    return mime


def new_file_id() -> str:
    return uuid.uuid4().hex


def new_file_entry(*, kind: str, name: str, mime: str, size: int, ref: str) -> dict[str, Any]:
    return {
        "kind": kind,
        # 파일 이름도 답처럼 가린다(휴대전화·이메일이 든 이름) — Admin 목록이 이 이름을 그대로 보인다.
        "name": mask_personal_info((name or "").strip()[:200]) or f"파일.{_EXTENSIONS.get(mime, 'bin')}",
        "mime": mime,
        "size": size,
        "ref": ref,
        "uploaded_at": datetime.now(UTC).isoformat(),
    }


def storage_filename(file_id: str, mime: str) -> str:
    return f"doctor-survey-{file_id}.{_EXTENSIONS.get(mime, 'bin')}"


def _keep_known(value: Any, known: set[str]) -> Any:
    if isinstance(value, list):
        return [item for item in value if not isinstance(item, Mapping) or item.get("id") in known]
    return value


def drop_unknown_files(answers: dict[str, Any], files: Mapping[str, Any]) -> dict[str, Any]:
    """답의 파일 칸에서 이 설문에 올린 적 없는 파일 id를 뺀다(다른 설문의 파일을 가리키지 못하게)."""

    known = set(files or {})
    out = dict(answers)
    for key, spec in schema.TOP_FIELDS.items():
        if spec.get("t") == "files" and key in out:
            out[key] = _keep_known(out[key], known)
            if not out[key]:
                out.pop(key)
        if spec.get("t") == "list" and isinstance(out.get(key), list):
            child_files = [c for c in schema.iter_fields(spec.get("fields") or []) if c.get("t") == "files"]
            if not child_files:
                continue
            rows = []
            for row in out[key]:
                row = dict(row)
                for child in child_files:
                    child_key = str(child["key"])
                    if child_key in row:
                        row[child_key] = _keep_known(row[child_key], known)
                        if not row[child_key]:
                            row.pop(child_key)
                rows.append(row)
            out[key] = rows
    return out


def referenced_files(answers: Mapping[str, Any] | None, files: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """답이 가리키는 파일 — AE 화면에 보일 목록(올렸다가 지운 파일은 빠진다)."""

    answers = answers or {}
    files = files or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(items: Any, context: str | None) -> None:
        for item in items or []:
            file_id = item.get("id") if isinstance(item, Mapping) else None
            entry = files.get(file_id) if file_id else None
            if not entry or file_id in seen:
                continue
            seen.add(file_id)
            out.append(
                {
                    "id": file_id,
                    "kind": entry.get("kind"),
                    "name": entry.get("name"),
                    "mime": entry.get("mime"),
                    "size": entry.get("size"),
                    "uploaded_at": entry.get("uploaded_at"),
                    "context": context,
                }
            )

    add(answers.get("photos"), None)
    add(answers.get("logo_files"), None)
    for row in answers.get("certifications") or []:
        if isinstance(row, Mapping):
            add(row.get("files"), str(row.get("name") or "") or None)
    return out


__all__ = (
    "KINDS",
    "MAX_FILE_BYTES",
    "MAX_FILES_PER_SURVEY",
    "SurveyFileError",
    "check_upload",
    "drop_unknown_files",
    "new_file_entry",
    "new_file_id",
    "referenced_files",
    "sniff_type",
    "storage_filename",
)

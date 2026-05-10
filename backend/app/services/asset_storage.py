"""Hospital source asset 파일 저장.

Source asset은 공개 승인 전까지 private 자료다. DB에는 직접 접근 가능한 public
URL을 저장하지 않고, local:// 또는 gs:// reference만 저장한다. 실제 다운로드/노출은
admin/public API가 권한과 is_public 플래그를 확인한 뒤 처리한다.
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

LOCAL_UPLOAD_DIR = Path("/tmp/private_asset_uploads")
LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def is_gcs_configured() -> bool:
    """GCS 사용 가능 여부.

    PROJECT_ID + BUCKET이 셋팅되어 있고, GOOGLE_APPLICATION_CREDENTIALS가 실제 파일을
    가리킬 때만 True. placeholder("REPLACE_ME") 또는 /dev/null fallback일 때는 False.
    """
    if not (settings.GCP_PROJECT_ID and settings.GCP_STORAGE_BUCKET):
        return False
    if settings.GCP_PROJECT_ID.upper() == "REPLACE_ME":
        return False
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""
    if not cred or cred == "/dev/null" or not Path(cred).exists():
        return False
    return True


def store_asset_bytes(*, hospital_id: uuid.UUID, filename: str, data: bytes, mime_type: str) -> str:
    """파일 바이트를 저장하고 private storage reference를 반환한다."""
    safe_filename = _sanitize_filename(filename)
    file_id = f"{uuid.uuid4()}-{safe_filename}"
    if is_gcs_configured():
        return _upload_to_gcs(hospital_id, file_id, data, mime_type)
    return _store_local(hospital_id, file_id, data)


def _sanitize_filename(filename: str) -> str:
    base = os.path.basename(filename or "asset")
    cleaned = "".join(c if c.isalnum() or c in {".", "-", "_"} else "_" for c in base)
    return cleaned[:120] or "asset"


def _store_local(hospital_id: uuid.UUID, file_id: str, data: bytes) -> str:
    hospital_dir = LOCAL_UPLOAD_DIR / str(hospital_id)
    hospital_dir.mkdir(parents=True, exist_ok=True)
    path = hospital_dir / file_id
    path.write_bytes(data)
    return f"local://{hospital_id}/{file_id}"


def _upload_to_gcs(hospital_id: uuid.UUID, file_id: str, data: bytes, mime_type: str) -> str:
    # google-cloud-storage 클라이언트는 image_engine.py 등에서 이미 사용 중.
    from google.cloud import storage  # noqa: WPS433 — 지연 import (서비스 임포트 사이클 방지)

    client = storage.Client(project=settings.GCP_PROJECT_ID)
    bucket = client.bucket(settings.GCP_STORAGE_BUCKET)
    blob_path = f"assets/{hospital_id}/{file_id}"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(data, content_type=mime_type)
    return f"gs://{settings.GCP_STORAGE_BUCKET}/{blob_path}"


def resolve_local_asset_path(asset_ref: str, *, expected_hospital_id: uuid.UUID | None = None) -> Path | None:
    """Resolve local:// refs without allowing path traversal or cross-hospital access."""
    if not asset_ref.startswith("local://"):
        return None
    raw = asset_ref[len("local://"):]
    parts = raw.split("/", 1)
    if len(parts) != 2:
        return None
    hospital_id, filename = parts
    if expected_hospital_id is not None and hospital_id != str(expected_hospital_id):
        return None
    if "/" in filename or "\\" in filename or filename in {"", ".", ".."}:
        return None
    path = (LOCAL_UPLOAD_DIR / hospital_id / filename).resolve()
    root = LOCAL_UPLOAD_DIR.resolve()
    if root not in path.parents:
        return None
    return path


def resolve_legacy_asset_path(asset_ref: str, *, expected_hospital_id: uuid.UUID | None = None) -> Path | None:
    """Resolve legacy /assets/{hospital_id}/{filename} refs after static mount removal."""
    if not asset_ref.startswith("/assets/"):
        return None
    raw = asset_ref[len("/assets/"):]
    parts = raw.split("/", 1)
    if len(parts) != 2:
        return None
    hospital_id, filename = parts
    return resolve_local_asset_path(
        f"local://{hospital_id}/{filename}",
        expected_hospital_id=expected_hospital_id,
    )


def is_private_asset_ref(asset_ref: str | None) -> bool:
    return bool(asset_ref and (asset_ref.startswith("local://") or asset_ref.startswith("gs://")))

"""Hospital source asset 파일 저장.

- prod: GCS 버킷에 업로드 후 public URL 반환 (GCP_PROJECT_ID + GCP_STORAGE_BUCKET 셋팅 시)
- dev: /tmp/asset_uploads/ 로컬 저장 + /assets/{file_id} static endpoint 통해 서빙

저장 위치는 환경변수에 따라 자동 분기. 호출자는 url만 받는다.
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

LOCAL_UPLOAD_DIR = Path("/tmp/asset_uploads")
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
    """파일 바이트를 저장하고 외부 접근 URL을 반환한다."""
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
    # /assets/{hospital_id}/{file_id} 라우트로 서빙됨 (main.py에 mount).
    return f"/assets/{hospital_id}/{file_id}"


def _upload_to_gcs(hospital_id: uuid.UUID, file_id: str, data: bytes, mime_type: str) -> str:
    # google-cloud-storage 클라이언트는 image_engine.py 등에서 이미 사용 중.
    from google.cloud import storage  # noqa: WPS433 — 지연 import (서비스 임포트 사이클 방지)

    client = storage.Client(project=settings.GCP_PROJECT_ID)
    bucket = client.bucket(settings.GCP_STORAGE_BUCKET)
    blob_path = f"assets/{hospital_id}/{file_id}"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(data, content_type=mime_type)
    return blob.public_url

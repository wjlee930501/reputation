import uuid
from pathlib import Path

from app.services import asset_storage
from app.services.asset_storage import is_gcs_configured, store_asset_bytes


def test_is_gcs_configured_false_without_project_or_bucket(monkeypatch):
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "")
    assert is_gcs_configured() is False


def test_is_gcs_configured_false_with_placeholder_project(monkeypatch):
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "REPLACE_ME")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "any-bucket")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/dev/null")
    assert is_gcs_configured() is False


def test_is_gcs_configured_false_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "real-project")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "real-bucket")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert is_gcs_configured() is False


def test_is_gcs_configured_false_when_credentials_path_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "real-project")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "real-bucket")
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing-credentials.json")
    )
    assert is_gcs_configured() is False


def test_is_gcs_configured_true_with_full_setup(monkeypatch, tmp_path):
    cred = tmp_path / "service-account.json"
    cred.write_text("{}")
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "real-project")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "real-bucket")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(cred))
    assert is_gcs_configured() is True


def test_store_asset_bytes_local_writes_file_and_returns_assets_url(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "")

    hospital_id = uuid.uuid4()
    payload = b"\x89PNG\r\n\x1a\n-fake-pixel"
    url = store_asset_bytes(
        hospital_id=hospital_id,
        filename="doctor portrait.png",
        data=payload,
        mime_type="image/png",
    )

    assert url.startswith(f"/assets/{hospital_id}/")
    relative = url[len("/assets/") :]
    saved = tmp_path / relative
    assert saved.read_bytes() == payload
    # 파일명 sanitization: 공백은 _ 로 치환되어야 한다.
    assert " " not in saved.name


def test_store_asset_bytes_sanitizes_path_traversal_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "")

    hospital_id = uuid.uuid4()
    url = store_asset_bytes(
        hospital_id=hospital_id,
        filename="../../etc/passwd",
        data=b"x",
        mime_type="text/plain",
    )

    relative = url[len("/assets/") :]
    saved_path = tmp_path / relative
    # path traversal 방지: 결과 경로가 hospital 디렉토리 내부에 있어야 한다.
    assert tmp_path / str(hospital_id) in saved_path.parents
    assert ".." not in saved_path.name

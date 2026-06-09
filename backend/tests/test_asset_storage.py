import uuid

from app.services import asset_storage
from app.services.asset_storage import is_gcs_configured, resolve_local_asset_path, store_asset_bytes


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
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert is_gcs_configured() is False


def test_is_gcs_configured_false_when_credentials_path_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "real-project")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "real-bucket")
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing-credentials.json")
    )
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert is_gcs_configured() is False


def test_is_gcs_configured_true_on_cloud_run_metadata_adc(monkeypatch):
    # Cloud Run: 키 파일 없이 메타데이터 서버 ADC — K_SERVICE env가 신호.
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "real-project")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "real-bucket")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("K_SERVICE", "reputation-api")
    assert is_gcs_configured() is True


def test_is_gcs_configured_true_with_full_setup(monkeypatch, tmp_path):
    cred = tmp_path / "service-account.json"
    cred.write_text("{}")
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "real-project")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "real-bucket")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(cred))
    assert is_gcs_configured() is True


def test_store_asset_bytes_local_writes_file_and_returns_private_ref(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "")

    hospital_id = uuid.uuid4()
    payload = b"\x89PNG\r\n\x1a\n-fake-pixel"
    asset_ref = store_asset_bytes(
        hospital_id=hospital_id,
        filename="doctor portrait.png",
        data=payload,
        mime_type="image/png",
    )

    assert asset_ref.startswith(f"local://{hospital_id}/")
    saved = resolve_local_asset_path(asset_ref, expected_hospital_id=hospital_id)
    assert saved is not None
    assert saved.read_bytes() == payload
    # 파일명 sanitization: 공백은 _ 로 치환되어야 한다.
    assert " " not in saved.name


def test_store_asset_bytes_sanitizes_path_traversal_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(asset_storage.settings, "GCP_PROJECT_ID", "")
    monkeypatch.setattr(asset_storage.settings, "GCP_STORAGE_BUCKET", "")

    hospital_id = uuid.uuid4()
    asset_ref = store_asset_bytes(
        hospital_id=hospital_id,
        filename="../../etc/passwd",
        data=b"x",
        mime_type="text/plain",
    )

    saved_path = resolve_local_asset_path(asset_ref, expected_hospital_id=hospital_id)
    assert saved_path is not None
    # path traversal 방지: 결과 경로가 hospital 디렉토리 내부에 있어야 한다.
    assert tmp_path / str(hospital_id) in saved_path.parents
    assert ".." not in saved_path.name


def test_resolve_local_asset_path_blocks_cross_hospital_access(monkeypatch, tmp_path):
    monkeypatch.setattr(asset_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    hospital_id = uuid.uuid4()
    other_hospital_id = uuid.uuid4()

    asset_ref = store_asset_bytes(
        hospital_id=hospital_id,
        filename="doctor.png",
        data=b"x",
        mime_type="image/png",
    )

    assert resolve_local_asset_path(asset_ref, expected_hospital_id=other_hospital_id) is None

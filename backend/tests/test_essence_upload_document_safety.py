"""문서 자료는 저장할 수 있는 형식만 받고, 내려줄 때는 실행되지 않게 내려준다.

추출기가 없는 바이트(.html 등)를 그대로 저장하면 본문도 못 뽑고, 파일 경로가 올라온
MIME 그대로 inline으로 돌려주어 다른 운영자의 브라우저에서 실행된다.
"""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from slowapi import Limiter

from app.api.admin.essence import source_file_safety_headers
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.essence import SourceType
from app.models.hospital import Hospital, HospitalStatus
from app.services import asset_storage

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


class FakeDB:
    """`db.get(Model, id)`만 쓰는 읽기 전용 경로를 위한 최소 대역."""

    def __init__(self, rows: dict[type, object]):
        self._rows = rows

    async def get(self, model, object_id):
        row = self._rows.get(model)
        if row is None or row.id != object_id:
            return None
        return row


def _hospital() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status=HospitalStatus.PENDING_DOMAIN,
        site_live=False,
        treatments=[],
    )


def _request(rows: dict[type, object], call):
    async def override_get_db():
        yield FakeDB(rows)

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            return call(client)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter


def test_unsupported_document_bytes_are_rejected_before_storage():
    hospital = _hospital()

    response = _request(
        {Hospital: hospital},
        lambda client: client.post(
            f"/api/v1/admin/hospitals/{hospital.id}/essence/sources/upload",
            headers=ADMIN_HEADERS,
            data={"source_type": SourceType.OTHER.value, "title": "안내"},
            files={"file": ("notice.html", b"<script>alert(1)</script>", "text/html")},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "PDF·DOCX 파일만 올릴 수 있습니다."


def test_document_file_is_served_as_a_download(tmp_path, monkeypatch):
    hospital_id = uuid.uuid4()
    monkeypatch.setattr(asset_storage, "LOCAL_UPLOAD_DIR", tmp_path)
    directory = tmp_path / str(hospital_id)
    directory.mkdir()
    (directory / "brochure.pdf").write_bytes(b"%PDF-1.7\n")
    source = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        source_type=SourceType.BROCHURE,
        file_url=f"local://{hospital_id}/brochure.pdf",
        mime_type="application/pdf",
    )

    from app.models.essence import HospitalSourceAsset

    response = _request(
        {HospitalSourceAsset: source},
        lambda client: client.get(
            f"/api/v1/admin/hospitals/{hospital_id}/essence/sources/{source.id}/file",
            headers=ADMIN_HEADERS,
        ),
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_photo_file_stays_inline():
    headers = source_file_safety_headers(SourceType.PHOTO_CLINIC_EXTERIOR, "image/png")
    assert headers["Content-Disposition"] == "inline"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_a_photo_row_with_a_document_mime_is_not_inline():
    """사진 행이라도 저장된 MIME이 이미지가 아니면 내려받게 한다."""
    headers = source_file_safety_headers(SourceType.PHOTO_CLINIC_EXTERIOR, "text/html")
    assert headers["Content-Disposition"] == "attachment"

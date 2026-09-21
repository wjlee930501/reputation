import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from test_admin_hospitals_profile import FakeDB, _hospital
from test_hospital_logo import _asgi_request

from app.api.admin import hospitals as admin
from app.api.public import assets, site
from app.core.config import settings

HOSPITAL_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
INVALID_REFS = [
    f"gs://other-bucket/assets/{HOSPITAL_ID}/logo.png",
    f"gs://test-images/assets/{OTHER_ID}/logo.png",
    f"gs://test-images/reports/{HOSPITAL_ID}/report.pdf",
    f"gs://test-images/assets/{HOSPITAL_ID}/report.pdf",
    f"gs://test-images/assets/{HOSPITAL_ID}/../report.png",
    f"local://{OTHER_ID}/logo.png",
    f"/assets/{HOSPITAL_ID}/report.pdf",
]


@pytest.mark.parametrize(
    "reference", INVALID_REFS + [f"gs://test-images/assets/{HOSPITAL_ID}/logo.png"]
)
async def test_profile_cannot_set_a_new_storage_reference(reference):
    hospital = _hospital(id=HOSPITAL_ID, logo_url=None, profile_complete=False)
    db = FakeDB(hospital)
    with pytest.raises(HTTPException) as error:
        await admin.update_profile(
            hospital.id, admin.HospitalProfileUpdate(logo_url=reference), BackgroundTasks(), db=db
        )
    assert error.value.status_code == 400
    assert hospital.logo_url is None
    assert not db.committed


@pytest.mark.parametrize("reference", INVALID_REFS)
async def test_invalid_legacy_logo_is_hidden_and_never_signed(reference, monkeypatch):
    monkeypatch.setattr(settings, "GCP_STORAGE_BUCKET", "test-images")
    hospital = _hospital(id=HOSPITAL_ID, logo_url=reference)
    monkeypatch.setattr(site, "_get_active_hospital", AsyncMock(return_value=hospital))
    signer = Mock(return_value="https://signed.example/logo")
    monkeypatch.setattr(assets, "get_signed_url", signer)
    handler = getattr(site.get_public_hospital_logo, "__wrapped__", site.get_public_hospital_logo)
    with pytest.raises(HTTPException) as error:
        await handler(request=_asgi_request(), slug=hospital.slug, db=None)
    assert error.value.status_code == 404
    assert site._public_logo_url(hospital) is None
    signer.assert_not_called()


async def test_own_bucket_image_is_signed(monkeypatch):
    monkeypatch.setattr(settings, "GCP_STORAGE_BUCKET", "test-images")
    reference = f"gs://test-images/assets/{HOSPITAL_ID}/logo.png"
    hospital = _hospital(id=HOSPITAL_ID, logo_url=reference)
    monkeypatch.setattr(site, "_get_active_hospital", AsyncMock(return_value=hospital))
    signer = Mock(return_value="https://signed.example/logo")
    monkeypatch.setattr(assets, "get_signed_url", signer)
    handler = getattr(site.get_public_hospital_logo, "__wrapped__", site.get_public_hospital_logo)
    response = await handler(request=_asgi_request(), slug=hospital.slug, db=None)
    assert response.status_code == 302
    signer.assert_called_once_with(reference)


@pytest.mark.parametrize(
    "reference", INVALID_REFS + ["https://legacy.example/logo.png", "malformed-legacy-ref"]
)
async def test_unchanged_legacy_profile_and_removal_remain_allowed(reference):
    hospital = _hospital(id=HOSPITAL_ID, logo_url=reference, profile_complete=False, treatments=[])
    db = FakeDB(hospital)
    await admin.update_profile(
        hospital.id,
        admin.HospitalProfileUpdate(logo_url=reference, phone="02-111-2222"),
        BackgroundTasks(),
        db=db,
    )
    assert hospital.logo_url == reference
    assert hospital.phone == "02-111-2222"
    await admin.update_profile(
        hospital.id, admin.HospitalProfileUpdate(logo_url=None), BackgroundTasks(), db=db
    )
    assert hospital.logo_url is None


@pytest.mark.parametrize("valid", [True, False])
async def test_upload_verifies_bytes_before_storage(valid, monkeypatch):
    from io import BytesIO

    from fastapi import UploadFile
    from PIL import Image
    from starlette.datastructures import Headers

    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    payload = buffer.getvalue() if valid else b"%PDF-not-an-image"
    hospital = _hospital(id=HOSPITAL_ID, logo_url=None, profile_complete=False)
    db = FakeDB(hospital)
    monkeypatch.setattr(admin, "write_audit_log", AsyncMock())
    storage = Mock(return_value=f"local://{HOSPITAL_ID}/logo.png")
    monkeypatch.setattr(admin, "store_asset_bytes", storage)
    upload = UploadFile(
        file=BytesIO(payload), filename="report.pdf", headers=Headers({"content-type": "image/png"})
    )
    if valid:
        response = await admin.upload_hospital_logo(hospital.id, file=upload, db=db)
        assert response["stored"] is True
        assert hospital.logo_url == storage.return_value
        assert storage.call_args.kwargs["hospital_id"] == hospital.id
        assert storage.call_args.kwargs["filename"] == "logo.png"
    else:
        with pytest.raises(HTTPException) as error:
            await admin.upload_hospital_logo(hospital.id, file=upload, db=db)
        assert error.value.status_code == 400
        storage.assert_not_called()


@pytest.mark.parametrize(
    "image_format,mime_type,extension",
    [("PNG", "image/png", "png"), ("JPEG", "image/jpeg", "jpg"), ("WEBP", "image/webp", "webp")],
)
def test_verified_upload_formats_and_mime_mismatch(image_format, mime_type, extension):
    from io import BytesIO

    from PIL import Image

    from app.services.hospital_logo import validated_logo_filename

    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format=image_format)
    assert validated_logo_filename(buffer.getvalue(), mime_type) == f"logo.{extension}"
    with pytest.raises(HTTPException) as error:
        validated_logo_filename(buffer.getvalue(), "image/gif")
    assert error.value.status_code == 400

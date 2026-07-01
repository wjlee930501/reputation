import uuid
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import Response

from app.services.asset_storage import resolve_legacy_asset_path, resolve_local_asset_path
from app.services.gcs_utils import get_signed_url


def public_asset_url(slug: str, source_id: uuid.UUID) -> str:
    return f"/api/v1/public/hospitals/{slug}/assets/{source_id}"


def public_asset_response(asset_ref: str, *, hospital_id: uuid.UUID, media_type: str | None) -> Response:
    if asset_ref.startswith("local://"):
        path = resolve_local_asset_path(asset_ref, expected_hospital_id=hospital_id)
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(path, media_type=media_type)
    if asset_ref.startswith("gs://"):
        signed_url = get_signed_url(asset_ref)
        if not signed_url or signed_url == asset_ref:
            raise HTTPException(status_code=503, detail="Could not create signed asset URL")
        return RedirectResponse(url=signed_url, status_code=302)
    if asset_ref.startswith("/assets/"):
        path = resolve_legacy_asset_path(asset_ref, expected_hospital_id=hospital_id)
        if path and path.exists():
            return FileResponse(path, media_type=media_type)
        raise HTTPException(status_code=404, detail="Asset not found")
    if _is_external_url(asset_ref):
        raise HTTPException(status_code=404, detail="Asset not found")
    raise HTTPException(status_code=404, detail="Asset not found")


def _is_external_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

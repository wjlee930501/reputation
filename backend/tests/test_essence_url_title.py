import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import essence as essence_api
from app.services.asset_extractor import FetchQuality


class FakeDB:
    def __init__(self, hospital_id):
        self.hospital_id = hospital_id

    async def get(self, _model, object_id):
        return SimpleNamespace(id=object_id) if object_id == self.hospital_id else None


@pytest.mark.asyncio
async def test_url_title_preview_returns_fetched_html_title(monkeypatch):
    hospital_id = uuid.uuid4()

    async def fake_fetch(_url):
        return "본문", None, FetchQuality(200, False, 0.0, "병원 진료 안내")

    monkeypatch.setattr(essence_api, "fetch_url_text", fake_fetch)

    result = await essence_api.preview_source_url_title(
        hospital_id,
        essence_api.SourceUrlTitleRequest(url="https://clinic.example/guide"),
        db=FakeDB(hospital_id),
    )

    assert result == {"title": "병원 진료 안내"}


@pytest.mark.asyncio
async def test_url_title_preview_keeps_manual_title_fallback_actionable(monkeypatch):
    hospital_id = uuid.uuid4()

    async def fake_fetch(_url):
        return "본문", None, FetchQuality(200, False, 0.0, None)

    monkeypatch.setattr(essence_api, "fetch_url_text", fake_fetch)

    with pytest.raises(HTTPException) as exc:
        await essence_api.preview_source_url_title(
            hospital_id,
            essence_api.SourceUrlTitleRequest(url="https://clinic.example/guide"),
            db=FakeDB(hospital_id),
        )

    assert exc.value.status_code == 422
    assert "직접 입력" in exc.value.detail

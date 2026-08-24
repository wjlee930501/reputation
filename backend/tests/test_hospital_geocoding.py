from types import SimpleNamespace

import httpx
import pytest

from app.services import hospital_geocoding as geocoding


@pytest.mark.asyncio
async def test_geocode_uses_one_capped_korean_lookup(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"lat": "37.566535", "lon": "126.9779692", "display_name": "서울특별시"}]

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(geocoding.httpx, "AsyncClient", Client)

    result = await geocoding.geocode_address(" 서울   중구 세종대로 110 ")

    assert result.latitude == 37.566535
    assert result.longitude == 126.977969
    assert len(calls) == 1
    assert calls[0][1]["params"]["limit"] == geocoding.GEOCODE_RESULT_LIMIT == 1
    assert calls[0][1]["params"]["countrycodes"] == "kr"


@pytest.mark.asyncio
async def test_geocode_returns_concrete_no_match_error(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [])

    monkeypatch.setattr(geocoding.httpx, "AsyncClient", Client)

    with pytest.raises(geocoding.GeocodingError, match="좌표를 찾지 못했습니다"):
        await geocoding.geocode_address("존재하지 않는 주소")


@pytest.mark.asyncio
async def test_geocode_does_not_retry_network_failures(monkeypatch):
    calls = 0

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(geocoding.httpx, "AsyncClient", Client)

    with pytest.raises(geocoding.GeocodingError, match="응답하지 않습니다"):
        await geocoding.geocode_address("서울 중구 세종대로 110")
    assert calls == 1

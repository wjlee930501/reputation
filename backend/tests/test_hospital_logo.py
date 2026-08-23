"""공식 로고 정책 — 저장 가능한 값과 공개 표면이 실제로 렌더할 값이 어긋나지 않는가.

L-1 회귀 가드. 어드민이 아무 URL이나 받고 공개 표면은 조용히 버리던 상태에서는,
운영자가 필수 게이트를 `승인됨`으로 통과시키고도 화면에는 아무 로고가 없었다.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.public import site as public_site
from app.services.hospital_logo import (
    is_external_logo_url,
    is_stored_logo_ref,
    public_logo_url,
)


def _hospital(logo_url, *, slug="test-clinic"):
    return SimpleNamespace(id=uuid.uuid4(), slug=slug, logo_url=logo_url)


@pytest.mark.parametrize(
    "value",
    [
        "gs://reputation-images/assets/abc/logo.png",
        "local://abc/logo.png",
        "/assets/abc/logo.png",
    ],
)
def test_uploaded_asset_refs_can_be_served(value):
    assert is_stored_logo_ref(value) is True
    assert is_external_logo_url(value) is False


@pytest.mark.parametrize(
    "value",
    [
        "https://cdn.imweb.me/thumbnail/logo.png",
        "http://example.com/logo.png",
    ],
)
def test_external_urls_are_not_storable(value):
    # 공개 표면의 자산 허용 목록이 거부하는 값 — 저장 단계에서 걸러야 조용히 사라지지 않는다.
    assert is_stored_logo_ref(value) is False
    assert is_external_logo_url(value) is True


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_values_are_neither_stored_nor_external(value):
    assert is_stored_logo_ref(value) is False
    assert is_external_logo_url(value) is False


def test_public_payload_serves_uploaded_logos_from_our_own_origin():
    # 백엔드 오리진 경로여야 공개 표면(resolveAssetUrl)이 통과시킨다.
    hospital = _hospital("gs://reputation-images/assets/abc/logo.png")

    assert public_site._public_logo_url(hospital) == public_logo_url(hospital.slug)


def test_public_payload_drops_a_legacy_external_logo_instead_of_promising_it():
    # 화면이 못 쓰는 값을 내려보내면 헤더는 비고 JSON-LD에는 깨진 주소가 남는다.
    hospital = _hospital("https://cdn.imweb.me/thumbnail/logo.png")

    assert public_site._public_logo_url(hospital) is None


async def test_the_public_logo_route_never_proxies_an_external_url(monkeypatch):
    """이 라우트가 외부 주소를 대신 받아다 주는 통로가 되면 안 된다."""
    hospital = _hospital("https://cdn.imweb.me/thumbnail/logo.png")

    async def fake_get_active_hospital(db, slug):
        return hospital

    monkeypatch.setattr(public_site, "_get_active_hospital", fake_get_active_hospital)

    # 라우트 함수는 slowapi 레이트리미터로 감싸여 있어 진짜 Request를 요구한다.
    # 여기서 검증할 것은 자산 정책이므로 원 함수를 직접 부른다.
    handler = public_site.get_public_hospital_logo
    handler = getattr(handler, "__wrapped__", handler)

    with pytest.raises(HTTPException) as exc:
        await handler(request=_asgi_request(), slug=hospital.slug, db=None)

    assert exc.value.status_code == 404


def _asgi_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/public/hospitals/test-clinic/logo",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )

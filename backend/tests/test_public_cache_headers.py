"""공개 API 캐시 헤더 — 무료 진단 표면은 CDN에 남으면 안 된다.

`PublicApiCacheMiddleware`는 `/api/v1/public/*` GET에 `public, max-age=300`을 붙인다.
병원 공개 정보에는 맞지만, **무료 진단은 개인에게 발급된 토큰으로 열리는 표면**이라
같은 규칙이 적용되면 CDN이 한 사람의 리포트를 저장했다가 같은 URL을 요청한 다른
사람에게 내줄 수 있다(PRD F5-4).

미들웨어는 라우트가 응답을 만든 **뒤에** 헤더를 덮으므로, 라우트에서 no-store를 달아도
여기서 검증하지 않으면 조용히 덮인 채로 배포된다. 그래서 라우트가 아니라
**미들웨어를 직접** 시험한다 — DB 없이 돌고, 덮어쓰기 규칙 자체를 겨눈다.
"""
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.main import PublicApiCacheMiddleware


@pytest.fixture
def client():
    """미들웨어만 얹은 최소 앱. 라우트는 프로덕션과 같은 경로 모양을 흉내낸다."""
    app = FastAPI()
    app.add_middleware(PublicApiCacheMiddleware)

    @app.get("/api/v1/public/diagnosis/slots")
    async def slots():
        return {"remaining": 7}

    @app.get("/api/v1/public/diagnosis/{token}")
    async def report(token: str):
        # 라우트가 스스로 다는 헤더 — 미들웨어가 덮어쓰지 않는지 본다.
        return PlainTextResponse("pdf", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/public/diagnosis/{token}/status")
    async def status(token: str):
        return {"phase": "MEASURING"}

    @app.get("/api/v1/public/hospitals/{slug}")
    async def hospital(slug: str):
        return {"slug": slug}

    @app.get("/api/v1/public/hospitals/{slug}/contents")
    async def contents(slug: str):
        return []

    with TestClient(app) as test_client:
        yield test_client


def _cache_control(response) -> str:
    return response.headers.get("cache-control", "")


class TestDiagnosisSurfaceIsNeverCached:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/public/diagnosis/slots",
            "/api/v1/public/diagnosis/some-token",
            "/api/v1/public/diagnosis/some-token/status",
        ],
    )
    def test_no_store(self, client, path):
        assert _cache_control(client.get(path)) == "no-store"

    def test_route_supplied_no_store_is_not_overwritten(self, client):
        """미들웨어가 라우트 헤더보다 나중에 실행된다 — 여기서 덮이면 라우트의 방어가 무의미하다."""
        response = client.get("/api/v1/public/diagnosis/some-token")
        assert "max-age" not in _cache_control(response)

    def test_noindex_and_referrer_are_stripped(self, client):
        response = client.get("/api/v1/public/diagnosis/some-token")
        assert "noindex" in response.headers.get("x-robots-tag", "")
        assert response.headers.get("referrer-policy") == "no-referrer"

    def test_slot_count_is_never_stale(self, client):
        """5분 캐시되면 마감된 뒤에도 랜딩에 '자리 있음'으로 보인다."""
        assert "max-age" not in _cache_control(client.get("/api/v1/public/diagnosis/slots"))


class TestOtherPublicSurfacesKeepTheirCdnHeaders:
    def test_hospital_surface_is_still_cacheable(self, client):
        """진단 예외가 공개 표면 전체의 캐시를 꺼버리면 CDN 이점이 사라진다."""
        cache_control = _cache_control(client.get("/api/v1/public/hospitals/abc"))
        assert cache_control.startswith("public, max-age=300")

    def test_content_list_keeps_its_shorter_ttl(self, client):
        cache_control = _cache_control(client.get("/api/v1/public/hospitals/abc/contents"))
        assert cache_control.startswith("public, max-age=60")

    def test_hospital_surface_is_not_marked_noindex(self, client):
        """병원 공개 정보는 검색엔진과 AI가 읽어야 하는 표면이다 — 여기에 noindex가 붙으면
        제품의 목적 자체가 무너진다."""
        assert client.get("/api/v1/public/hospitals/abc").headers.get("x-robots-tag") is None

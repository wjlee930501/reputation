"""IndexNow 제출 테스트.

이 기능이 조용히 망가지는 방식은 두 가지다. 둘 다 발행은 성공하고 색인만 안 된다.
  1. 커스텀 도메인 대신 플랫폼 호스트로 제출 → 정본 URL이 색인 신호를 못 받는다
  2. 호스트가 섞인 URL 목록 → IndexNow가 요청 전체를 거부한다
"""
import pytest

from app.core.config import settings
from app.services import indexnow


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.posts = []
        self._status = kwargs.pop("_status", 200)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return _FakeResponse(self._status)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "INDEXNOW_ENABLED", True)
    monkeypatch.setattr(settings, "INDEXNOW_KEY", "testkey123")
    return settings


# ── base URL 선택 ──


def test_custom_domain_wins_over_platform_host():
    # 자기 도메인을 연결한 병원은 그 도메인이 정본이다. 플랫폼 호스트로 제출하면
    # 환자가 실제로 보는 URL은 색인 신호를 받지 못한다.
    assert indexnow.public_base_url("jangclinic.kr") == "https://jangclinic.kr"
    assert indexnow.public_base_url("https://jangclinic.kr/") == "https://jangclinic.kr"


def test_falls_back_to_platform_host_without_custom_domain():
    assert indexnow.public_base_url(None) == settings.SITE_BASE_URL.rstrip("/")
    assert indexnow.public_base_url("  ") == settings.SITE_BASE_URL.rstrip("/")


# ── 제출 동작 ──


@pytest.mark.asyncio
async def test_skips_when_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "INDEXNOW_KEY", "")
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("키가 없으면 네트워크 호출을 하면 안 된다")

    monkeypatch.setattr(indexnow.httpx, "AsyncClient", _boom)
    assert await indexnow.submit_urls(base_url="https://x.kr", urls=["https://x.kr/a"]) is False
    assert called is False


@pytest.mark.asyncio
async def test_payload_shape(enabled, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    ok = await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    )

    assert ok is True
    url, payload = client.posts[0]
    assert url == indexnow.INDEXNOW_ENDPOINT
    assert payload["host"] == "jangclinic.kr"
    assert payload["key"] == "testkey123"
    # keyLocation은 제출 호스트와 같아야 소유 증명이 된다.
    assert payload["keyLocation"] == "https://jangclinic.kr/indexnow-key.txt"
    assert payload["urlList"] == ["https://jangclinic.kr/a"]


@pytest.mark.asyncio
async def test_drops_urls_from_other_hosts(enabled, monkeypatch):
    # 호스트가 섞이면 IndexNow는 요청 전체를 거부한다 — 미리 걸러낸다.
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    await indexnow.submit_urls(
        base_url="https://jangclinic.kr",
        urls=["https://jangclinic.kr/a", "https://other.kr/b"],
    )

    assert client.posts[0][1]["urlList"] == ["https://jangclinic.kr/a"]


@pytest.mark.asyncio
async def test_chunks_large_batches(enabled, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)
    urls = [f"https://jangclinic.kr/{i}" for i in range(indexnow.MAX_URLS_PER_REQUEST + 10)]

    await indexnow.submit_urls(base_url="https://jangclinic.kr", urls=urls)

    assert len(client.posts) == 2
    assert len(client.posts[0][1]["urlList"]) == indexnow.MAX_URLS_PER_REQUEST
    assert len(client.posts[1][1]["urlList"]) == 10


@pytest.mark.asyncio
async def test_content_submission_includes_the_new_page_and_its_listings(enabled, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    await indexnow.submit_content_published(
        slug="jangpyeonhanoegwayiweon", content_id="abc-123", aeo_domain="jangclinic.kr"
    )

    urls = client.posts[0][1]["urlList"]
    assert "https://jangclinic.kr/jangpyeonhanoegwayiweon/contents/abc-123" in urls
    # 새 글이 걸리는 목록/허브도 함께 알려야 링크가 발견된다.
    assert "https://jangclinic.kr/jangpyeonhanoegwayiweon/contents" in urls
    assert "https://jangclinic.kr/jangpyeonhanoegwayiweon/llms.txt" in urls
    # sitemap.xml은 색인 대상 문서가 아니다.
    assert not any(u.endswith("/sitemap.xml") for u in urls)


@pytest.mark.asyncio
async def test_safe_wrapper_swallows_errors(enabled, monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(indexnow.httpx, "AsyncClient", _explode)

    # 색인 신호 실패가 발행 파이프라인을 멈추게 하면 안 된다.
    assert (
        await indexnow.submit_content_published_safe(
            slug="s", content_id="c", aeo_domain="jangclinic.kr"
        )
        is False
    )


@pytest.mark.asyncio
async def test_non_2xx_reports_failure(enabled, monkeypatch):
    client = _FakeClient(_status=422)
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    assert await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    ) is False


# ── 백필용 URL 조립 ──


def test_hospital_all_urls_includes_hub_and_every_published_content():
    base, urls = indexnow.hospital_all_urls(
        slug="jangpyeonhanoegwayiweon",
        aeo_domain="jangclinic.kr",
        content_ids=["c1", "c2", "c3"],
    )

    assert base == "https://jangclinic.kr"
    for cid in ("c1", "c2", "c3"):
        assert f"https://jangclinic.kr/jangpyeonhanoegwayiweon/contents/{cid}" in urls
    assert "https://jangclinic.kr/jangpyeonhanoegwayiweon" in urls
    assert not any(u.endswith("/sitemap.xml") for u in urls)


def test_hospital_all_urls_has_no_duplicates():
    _, urls = indexnow.hospital_all_urls(
        slug="s", aeo_domain="x.kr", content_ids=["a", "a", "b"]
    )
    assert len(urls) == len(set(urls))

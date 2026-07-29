"""IndexNow 제출 테스트.

이 기능이 조용히 망가지는 방식은 두 가지다. 둘 다 발행은 성공하고 색인만 안 된다.
  1. 커스텀 도메인 대신 플랫폼 호스트로 제출 → 정본 URL이 색인 신호를 못 받는다
  2. 호스트가 섞인 URL 목록 → IndexNow가 요청 전체를 거부한다
"""
import pytest

from app.core.config import settings
from app.services import indexnow


class _FakeURL:
    def __init__(self, host: str):
        self.host = host


class _FakeResponse:
    def __init__(self, status_code=200, text="", host=""):
        self.status_code = status_code
        self.text = text
        # 소유 증명은 **최종 응답의 호스트**가 제출 호스트와 같은지까지 확인한다.
        self.url = _FakeURL(host)


class _FakeClient:
    """제출(POST)과 소유 증명 확인(GET)을 함께 흉내낸다.

    제출 전에 `{base}/indexnow-key.txt`가 설정과 같은 키를 주는지 확인하므로,
    기본값은 '키가 올바르게 서빙되는 site'다. 소유 증명 실패 상황은
    `_key_text`/`_key_status`로 표현한다.
    """

    def __init__(self, *args, **kwargs):
        self.posts = []
        self.gets = []
        self._status = kwargs.pop("_status", 200)
        # 어떤 옵션으로 클라이언트가 만들어졌는지 기록한다 — follow_redirects 회귀를
        # 테스트로 잡기 위해서다(리다이렉트를 따라가면 소유 증명이 무의미해진다).
        self.init_kwargs = dict(kwargs)
        _FakeClient.last_init_kwargs = dict(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.gets.append(url)
        return _FakeResponse(
            _FakeClient._key_status,
            _FakeClient._key_text,
            host=_FakeClient._key_host,
        )

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return _FakeResponse(self._status)


# 클래스 속성으로 둬서 인스턴스가 새로 만들어져도 시나리오가 유지된다.
_FakeClient._key_status = 200
_FakeClient._key_text = "testkey123"
# 기본값은 '제출 호스트가 직접 키를 응답하는' 정상 상태.
_FakeClient._key_host = "jangclinic.kr"
_FakeClient.last_init_kwargs = {}


@pytest.fixture(autouse=True)
def _reset_ownership_cache():
    """호스트별 소유 증명 캐시가 테스트 간에 새지 않게 한다."""
    indexnow._OWNERSHIP_CACHE.clear()
    _FakeClient._key_status = 200
    _FakeClient._key_text = "testkey123"
    _FakeClient._key_host = "jangclinic.kr"
    _FakeClient.last_init_kwargs = {}
    yield
    indexnow._OWNERSHIP_CACHE.clear()


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


# ── 소유 증명 ──
#
# backend(제출)와 site(키 파일 응답)는 별도 서비스로 배포된다. 키가 한쪽에만 있는
# 구간이 생기면 IndexNow는 소유를 증명할 수 없는 요청을 받는다. 배포 순서로 풀면
# out-of-band 배포에서 다시 깨지므로 런타임에서 확인하고, 실패하면 제출을 건너뛴다.


@pytest.mark.asyncio
async def test_does_not_submit_when_the_site_serves_no_key(enabled, monkeypatch):
    """site에 키가 아직 배포되지 않았으면(404) 제출하지 않는다."""
    _FakeClient._key_status = 404
    _FakeClient._key_text = "Not Found"
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    ok = await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    )

    assert ok is False
    assert client.posts == [], "소유 증명 실패인데 제출했다"


@pytest.mark.asyncio
async def test_does_not_submit_when_the_site_serves_a_different_key(enabled, monkeypatch):
    """site와 backend의 키가 다르면(서로 다른 배포 세대) 제출하지 않는다."""
    _FakeClient._key_status = 200
    _FakeClient._key_text = "some-older-key"
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    ok = await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    )

    assert ok is False
    assert client.posts == []


@pytest.mark.asyncio
async def test_submits_when_the_site_serves_the_matching_key(enabled, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    ok = await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    )

    assert ok is True
    assert len(client.posts) == 1
    assert client.gets == ["https://jangclinic.kr/indexnow-key.txt"]


@pytest.mark.asyncio
async def test_ownership_check_does_not_follow_redirects(enabled, monkeypatch):
    """리다이렉트를 따라가면 소유 증명이 무의미해진다.

    병원 도메인이 외부로 301하면 남의 서버가 준 키로 '증명됨'이 되고(소유 없는 호스트에
    제출), 목적지가 호스트명 allowlist를 우회하므로 워커가 대신 GET하는 SSRF가 된다.
    """
    seen_kwargs: list[dict] = []

    def _factory(*args, **kwargs):
        seen_kwargs.append(dict(kwargs))
        return _FakeClient(*args, **kwargs)

    monkeypatch.setattr(indexnow.httpx, "AsyncClient", _factory)

    await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    )

    # 첫 클라이언트가 소유 증명 GET용이다.
    assert seen_kwargs, "소유 증명 GET이 수행되지 않았다"
    assert seen_kwargs[0].get("follow_redirects") is False, (
        "소유 증명 GET이 리다이렉트를 따라가면 안 된다"
    )


@pytest.mark.asyncio
async def test_does_not_submit_when_the_key_comes_from_a_different_host(enabled, monkeypatch):
    """다른 호스트가 응답한 키는 제출 호스트의 소유를 증명하지 않는다."""
    _FakeClient._key_host = "someone-else.example"
    client = _FakeClient()
    monkeypatch.setattr(indexnow.httpx, "AsyncClient", lambda *a, **k: client)

    ok = await indexnow.submit_urls(
        base_url="https://jangclinic.kr", urls=["https://jangclinic.kr/a"]
    )

    assert ok is False
    assert client.posts == [], "다른 호스트가 준 키로 소유가 증명됐다"

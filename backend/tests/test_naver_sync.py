from types import SimpleNamespace
import uuid

import pytest

from app.models.essence import SourceStatus
from app.workers import naver_sync


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.added = []
        self.commits = 0

    def execute(self, _stmt):
        return _Rows(self.rows)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_sync_hospital_naver_sources_adds_new_posts_as_pending(monkeypatch):
    urls = [
        "https://blog.naver.com/sw_hang/111?fromRss=true",
        "https://blog.naver.com/sw_hang/222?fromRss=true",
    ]

    async def fake_urls(_ref, max_posts):
        assert max_posts == 15
        return urls, None

    async def fake_text(url):
        return f"{url} 본문 " + ("충분한 설명 " * 30), None, SimpleNamespace(looks_like_shell=False)

    monkeypatch.setattr(naver_sync, "fetch_naver_blog_post_urls", fake_urls)
    monkeypatch.setattr(naver_sync, "fetch_url_text", fake_text)
    # Legacy rows retained RSS tracking parameters while the new enumerator
    # returns canonical URLs. They must still compare as the same post.
    db = _DB(rows=[("https://blog.naver.com/sw_hang/111?trackingCode=legacy", "existing-hash")])
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        blog_url="https://blog.naver.com/sw_hang",
    )

    result = await naver_sync.sync_hospital_naver_sources(db, hospital)

    assert result.created == 1
    assert result.skipped_duplicate == 1
    assert db.commits == 1
    assert len(db.added) == 1
    assert db.added[0].status == SourceStatus.PENDING
    assert db.added[0].source_metadata["review_required"] is True


@pytest.mark.asyncio
async def test_sync_hospital_naver_sources_does_not_commit_on_rss_error(monkeypatch):
    async def fake_urls(_ref, max_posts):
        return [], "RSS unavailable"

    monkeypatch.setattr(naver_sync, "fetch_naver_blog_post_urls", fake_urls)
    db = _DB()
    hospital = SimpleNamespace(id=uuid.uuid4(), blog_url="https://blog.naver.com/sw_hang")

    result = await naver_sync.sync_hospital_naver_sources(db, hospital)

    assert result.error == "RSS unavailable"
    assert db.added == []
    assert db.commits == 0

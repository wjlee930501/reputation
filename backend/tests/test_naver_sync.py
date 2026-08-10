import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.celery_app import celery_app
from app.models.essence import HospitalSourceAsset, SourceStatus
from app.models.hospital import HospitalStatus
from app.services import naver_handoff, naver_handoff_sources
from app.workers import naver_sync

WEEKLY_TASK_NAME = "app.workers.naver_sync.weekly_naver_source_sync"


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

    async def execute(self, _stmt):
        return _Rows(self.rows)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None


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

    monkeypatch.setattr(naver_handoff, "fetch_naver_blog_post_urls", fake_urls)
    monkeypatch.setattr(naver_handoff, "fetch_url_text", fake_text)
    # Legacy rows retained RSS tracking parameters while the new enumerator
    # returns canonical URLs. They must still compare as the same post.
    db = _DB(rows=[("https://blog.naver.com/sw_hang/111?trackingCode=legacy", "existing-hash")])
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트 의원",
        blog_url="https://blog.naver.com/sw_hang",
    )

    result = await naver_sync.sync_hospital_naver_sources(db, hospital)

    assert result.created == 1
    assert result.skipped_duplicate == 1
    sources = [item for item in db.added if isinstance(item, HospitalSourceAsset)]
    assert len(sources) == 1
    assert sources[0].status == SourceStatus.PENDING
    assert sources[0].source_metadata["review_required"] is True


@pytest.mark.asyncio
async def test_sync_hospital_naver_sources_does_not_commit_on_rss_error(monkeypatch):
    async def fake_urls(_ref, max_posts):
        return [], "RSS unavailable"

    monkeypatch.setattr(naver_handoff, "fetch_naver_blog_post_urls", fake_urls)
    db = _DB()
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="테스트 의원", blog_url="https://blog.naver.com/sw_hang"
    )

    result = await naver_sync.sync_hospital_naver_sources(db, hospital)

    assert result.error == "네이버 글 목록을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요."
    sources = [item for item in db.added if isinstance(item, HospitalSourceAsset)]
    assert sources == []
    assert result.run_id is not None


@pytest.mark.asyncio
async def test_sync_handoff_keeps_mixed_url_outcomes_without_fake_evidence(monkeypatch):
    # Given: one valid post, one provider failure, and one empty page
    urls = [
        "https://blog.naver.com/sw_hang/101",
        "https://blog.naver.com/sw_hang/202",
        "https://blog.naver.com/sw_hang/303",
    ]

    async def fake_urls(_ref, max_posts):
        assert max_posts == 15
        return urls, None

    async def fake_text(url):
        if url.endswith("/202"):
            return "", "HTTP 500 — URL 접근 실패.", None
        if url.endswith("/303"):
            return "", None, SimpleNamespace(looks_like_shell=True)
        return "근거가 되는 병원 설명 " * 30, None, SimpleNamespace(looks_like_shell=False)

    async def fake_record_failure(_db, _context):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(naver_handoff, "fetch_naver_blog_post_urls", fake_urls)
    monkeypatch.setattr(naver_handoff, "fetch_url_text", fake_text)
    monkeypatch.setattr(naver_handoff_sources, "record_naver_failure", fake_record_failure)
    db = _DB()
    hospital = SimpleNamespace(
        id=uuid.uuid4(), name="테스트 의원", blog_url="https://blog.naver.com/sw_hang"
    )

    # When: the handoff processes every discovered URL
    result = await naver_sync.sync_hospital_naver_sources(db, hospital)

    # Then: every URL has a durable typed outcome and only real text becomes evidence
    assert [item.state.value for item in result.items] == ["INGESTED", "FAILED", "SKIPPED"]
    assert result.items[1].safe_error_code == "NAVER_HTTP_ERROR"
    assert "개발팀" in result.items[1].next_action
    assert result.items[2].safe_error_code == "EMPTY_CONTENT"
    sources = [item for item in db.added if isinstance(item, HospitalSourceAsset)]
    assert len(sources) == 1


# ── 주간 배치 등록 회귀 가드 ────────────────────────────────────────────
# sync_hospital_naver_sources는 오랫동안 테스트에서만 호출되는 죽은 경로였다.
# Celery 등록(include/task_routes/beat_schedule) 중 하나라도 빠지면 "주간 네이버
# 자동 인입"이 조용히 한 번도 실행되지 않는다.


def test_weekly_naver_sync_module_is_imported_by_workers():
    # 이 테스트 파일이 naver_sync를 직접 import하므로 celery_app.tasks 존재만으로는
    # 워커 프로세스에서의 등록을 증명하지 못한다 — include 목록을 직접 본다.
    assert "app.workers.naver_sync" in celery_app.conf.include
    assert WEEKLY_TASK_NAME in celery_app.tasks


def test_weekly_naver_sync_is_scheduled_and_routed():
    entry = celery_app.conf.beat_schedule["weekly-naver-source-sync"]
    assert entry["task"] == WEEKLY_TASK_NAME
    assert celery_app.conf.task_routes[WEEKLY_TASK_NAME]["queue"] == "default"
    # 주간 측정(월 02:00)과 겹치지 않게 화요일에 돈다.
    assert entry["schedule"].day_of_week == {2}


class _WeeklyDB:
    def __init__(self, hospitals):
        self._hospitals = hospitals
        self.rolled_back = 0

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._hospitals))

    async def rollback(self):
        self.rolled_back += 1

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def test_weekly_naver_sync_isolates_failures_and_notifies(monkeypatch):
    """한 병원의 크롤링 실패가 나머지 병원 인입을 막지 않고, 실패는 ops로 알린다."""
    hospitals = [
        SimpleNamespace(
            id=uuid.uuid4(), name="정상의원", status=HospitalStatus.ACTIVE,
            blog_url="https://blog.naver.com/ok",
        ),
        SimpleNamespace(
            id=uuid.uuid4(), name="차단의원", status=HospitalStatus.ACTIVE,
            blog_url="https://blog.naver.com/blocked",
        ),
    ]
    db = _WeeklyDB(hospitals)

    async def fake_sync(_db, hospital, **_kwargs):
        if hospital.name == "차단의원":
            raise SQLAlchemyError("database unavailable")
        return naver_sync.NaverSyncResult(blog_id="ok", requested=5, created=2)

    queued = []

    async def fake_enqueue(_db, intent):
        queued.append(intent)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(naver_sync, "get_async_sessionmaker", lambda: lambda: db)
    monkeypatch.setattr(naver_sync, "sync_hospital_naver_sources", fake_sync)
    monkeypatch.setattr(naver_sync, "enqueue_notification", fake_enqueue)

    summary = naver_sync.weekly_naver_source_sync()

    assert summary == {"processed": 1, "created": 2, "failed": 1}
    assert len(queued) == 1
    assert queued[0].notification_type == "NAVER_WEEKLY_HANDOFF"
    assert "새 자료 2개" in queued[0].message.fallback_text
    assert db.rolled_back == 1

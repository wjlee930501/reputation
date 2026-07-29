"""운영 기준 초안 생성이 이벤트 루프를 막지 않는다 — 동작으로 확인한다.

배경: Claude 합성(`synthesize_philosophy`)은 동기 SDK 호출이라 60초 타임아웃 가까이
걸릴 수 있다. 이벤트 루프에서 그대로 돌리면 /health/live가 굶어 Cloud Run이 멀쩡한
API 인스턴스를 죽인다.

이전 테스트는 `inspect.getsource`로 "await asyncio.to_thread(" 문자열을 찾았다.
주석 처리된 줄에도 통과하고, 동등한 리팩터(run_in_executor, 별도 헬퍼로 추출)에는
근거 없이 깨진다. 여기서는 합성을 실제로 블로킹시킨 뒤 (1) 그 호출이 루프 스레드가
아닌 워커 스레드에서 실행되는지, (2) 블로킹 동안 루프가 계속 도는지 확인한다.
"""
import asyncio
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from app.api.admin import essence

# 블로킹 합성이 차지하는 시간. 오프로딩되면 이 시간 동안 루프는 계속 돈다.
BLOCKING_SECONDS = 0.3
HEARTBEAT_INTERVAL = 0.01


class _FakeDB:
    """create_philosophy_draft가 마지막에 쓰는 최소 세션 계약."""

    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        return None


@pytest.fixture
def blocking_synthesis(monkeypatch):
    """Claude 합성을 '느린 동기 호출'로 바꾸고 실행 스레드를 기록한다."""
    hospital = SimpleNamespace(id=uuid.uuid4(), name="스레딩테스트병원", slug="threading-test")
    sources = [SimpleNamespace(id=uuid.uuid4())]
    notes = [SimpleNamespace(id=uuid.uuid4())]
    calls = {}

    async def _hospital(_db, _hospital_id):
        return hospital

    async def _sources(_db, _hospital_id, _ids):
        return sources

    async def _notes(_db, _source_ids):
        return notes

    async def _version(_db, _hospital_id):
        return 1

    def _synthesize(*_args, **_kwargs):
        calls["thread_id"] = threading.get_ident()
        calls["started_at"] = time.monotonic()
        time.sleep(BLOCKING_SECONDS)
        calls["finished_at"] = time.monotonic()
        return {}

    monkeypatch.setattr(essence, "_get_hospital_or_404", _hospital)
    monkeypatch.setattr(essence, "_select_processed_sources", _sources)
    monkeypatch.setattr(essence, "_get_notes_for_sources", _notes)
    monkeypatch.setattr(essence, "_next_version", _version)
    monkeypatch.setattr(essence, "synthesize_philosophy", _synthesize)
    monkeypatch.setattr(essence, "find_error_marker_fields", lambda _payload: [])
    monkeypatch.setattr(essence, "validate_philosophy_grounding", lambda _payload, _notes: [])
    monkeypatch.setattr(essence, "_serialize_philosophy", lambda philosophy: philosophy)
    return calls


async def _create_draft(db=None):
    from app.schemas.essence import PhilosophyDraftCreate

    return await essence.create_philosophy_draft(
        uuid.uuid4(),
        PhilosophyDraftCreate(created_by="AE"),
        db=db or _FakeDB(),
    )


async def test_philosophy_synthesis_runs_off_the_event_loop_thread(blocking_synthesis):
    loop_thread_id = threading.get_ident()

    await _create_draft()

    assert blocking_synthesis["thread_id"] != loop_thread_id, (
        "동기 Claude 합성이 이벤트 루프 스레드에서 실행됐다 — /health/live가 굶는다"
    )


async def test_event_loop_keeps_running_while_philosophy_synthesis_blocks(blocking_synthesis):
    ticks = 0
    stop = asyncio.Event()

    async def heartbeat():
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        await _create_draft()
    finally:
        stop.set()
        await beat

    # 오프로딩되면 0.3초 동안 10ms 하트비트가 수십 번 돈다. 루프에서 돌면 0에 가깝다.
    assert ticks >= 5, f"합성이 도는 동안 이벤트 루프가 {ticks}번밖에 못 돌았다"


async def test_philosophy_draft_still_commits_after_the_offloaded_synthesis(blocking_synthesis):
    """오프로딩이 결과를 버리지 않는다 — 초안은 그대로 저장돼야 한다."""
    db = _FakeDB()

    await _create_draft(db)

    assert db.commits == 1
    assert len(db.added) == 1
    assert db.added[0].version == 1
    assert db.added[0].created_by == "AE"

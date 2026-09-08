"""채널 자료 fetch 워커와 그 복구 스윕(B1) — 그리고 처리 실패의 운영 예외(B2).

프로파일 저장은 본문 없는 PENDING 행만 만든다. 이 파일은 그 뒤를 검사한다: 워커가 본문을
받아 처리로 잇는지, 영구 실패를 굳히는지, 일시 실패를 예산 안에서만 다시 거는지, 예산을 다
쓴 자료가 사람의 할 일이 되는지.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.essence import SourceStatus, SourceType
from app.services.essence_sources import (
    FetchedSourceContent,
    SourceRegistrationError,
    TransientSourceFetchError,
)
from app.workers import tasks


class _FakeSession:
    """`SyncSessionLocal()` 대신 쓰는 최소 세션. 저장소는 테스트가 들고 있는 dict다."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _model, object_id):
        return self.store.get(object_id)

    def execute(self, _stmt):
        rows = [
            row
            for row in self.store.values()
            if getattr(row, "source_type", None) is not None
            and row.status == SourceStatus.PENDING
            and row.raw_text is None
            and row.url
        ]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    def commit(self):
        pass


def _source(**overrides):
    values = dict(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        source_type=SourceType.HOMEPAGE,
        title="병원 홈페이지",
        url="https://clinic.example.com",
        raw_text=None,
        operator_note=None,
        content_hash=None,
        process_error=None,
        status=SourceStatus.PENDING,
        source_metadata={
            "channel_field": "website_url",
            "fetch_state": "QUEUED",
            "registered_from": "profile",
        },
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def worker_env(monkeypatch):
    """세션·처리 디스패치·예외 기록을 모두 관찰 가능한 double로 바꾼다."""
    store: dict[uuid.UUID, object] = {}
    calls = {"processing": [], "opened": [], "recovered": [], "dispatched": []}

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _FakeSession(store))
    monkeypatch.setattr(
        tasks,
        "_start_source_processing_sync",
        lambda hospital_id, source_id: calls["processing"].append((hospital_id, source_id)),
    )

    async def fake_open(**kwargs):
        calls["opened"].append(kwargs)
        return uuid.uuid4()

    async def fake_recover(**kwargs):
        calls["recovered"].append(kwargs)
        return True

    monkeypatch.setattr(tasks, "open_ops_incident", fake_open)
    monkeypatch.setattr(tasks, "recover_ops_incident", fake_recover)
    monkeypatch.setattr(
        tasks.fetch_channel_source,
        "apply_async",
        lambda args=None, **_kwargs: calls["dispatched"].append(list(args or [])),
    )
    return SimpleNamespace(store=store, calls=calls)


def _add(worker_env, source, *, hospital_name="테스트의원"):
    worker_env.store[source.id] = source
    worker_env.store[source.hospital_id] = SimpleNamespace(
        id=source.hospital_id, name=hospital_name
    )
    return source


def test_a_fetched_channel_source_gets_its_body_and_starts_processing(worker_env, monkeypatch):
    source = _add(worker_env, _source())

    async def fake_fetch(*, source_type, url, title=None):
        assert source_type == SourceType.HOMEPAGE
        assert url == "https://clinic.example.com"
        return FetchedSourceContent(title="장편한외과의원 소개", text="병원 소개 본문")

    monkeypatch.setattr(tasks, "fetch_source_content", fake_fetch)

    result = tasks.fetch_channel_source.apply(args=[str(source.id)]).get()

    assert result["status"] == "FETCHED"
    assert source.title == "장편한외과의원 소개"
    assert source.raw_text == "병원 소개 본문"
    assert source.content_hash
    assert source.status == SourceStatus.PENDING
    assert source.source_metadata["fetch_state"] == "FETCHED"
    assert source.source_metadata["crawled_at"]
    assert worker_env.calls["processing"] == [(source.hospital_id, source.id)]
    assert worker_env.calls["opened"] == []


def test_a_permanent_failure_stops_retrying_and_becomes_a_persons_job(worker_env, monkeypatch):
    source = _add(worker_env, _source(source_type=SourceType.NAVER_BLOG))

    async def shell_page(**_kwargs):
        raise SourceRegistrationError("네이버 블로그 본문을 가져오지 못했습니다 — 본문을 직접 붙여넣어 주세요.")

    monkeypatch.setattr(tasks, "fetch_source_content", shell_page)

    result = tasks.fetch_channel_source.apply(args=[str(source.id)]).get()

    assert result["status"] == SourceStatus.ERROR.value
    assert source.status == SourceStatus.ERROR
    assert source.process_error.startswith("네이버 블로그 본문")
    assert source.source_metadata["fetch_state"] == "FAILED"
    assert len(worker_env.calls["opened"]) == 1
    opened = worker_env.calls["opened"][0]
    assert opened["pipeline"] == "source_fetch"
    assert opened["object_id"] == str(source.id)
    assert opened["next_action"] == tasks.SOURCE_FETCH_NEXT_ACTION
    assert opened["admin_path"] == f"/hospitals/{source.hospital_id}/info"
    # 화면이 '운영 센터 확인' 링크를 걸 수 있게, 실제로 열린 예외만 붙는다.
    assert source.source_metadata["incident_id"]
    assert worker_env.calls["processing"] == []


def test_a_transient_failure_keeps_the_row_pending_for_the_next_attempt(worker_env, monkeypatch):
    source = _add(worker_env, _source())

    async def timeout(**_kwargs):
        raise TransientSourceFetchError("URL 크롤링 실패: 연결 시간이 초과되었습니다")

    monkeypatch.setattr(tasks, "fetch_source_content", timeout)

    # 예산을 다 쓴 마지막 시도 — 여기서는 예외를 열지 않고 스윕에 넘긴다.
    result = tasks.fetch_channel_source.apply(args=[str(source.id)], retries=3).get()

    assert result["status"] == "FAILED"
    assert source.status == SourceStatus.PENDING
    assert source.source_metadata["fetch_state"] == "FAILED"
    assert "연결 시간" in source.source_metadata["fetch_error"]
    assert worker_env.calls["opened"] == []


def test_the_first_attempt_is_counted_once_per_dispatch(worker_env, monkeypatch):
    source = _add(worker_env, _source(source_metadata={"fetch_state": "QUEUED", "fetch_attempts": 1}))

    async def fake_fetch(**_kwargs):
        return FetchedSourceContent(title="제목", text="본문")

    monkeypatch.setattr(tasks, "fetch_source_content", fake_fetch)
    tasks.fetch_channel_source.apply(args=[str(source.id)]).get()

    assert source.source_metadata["fetch_attempts"] == 2
    assert source.source_metadata["last_fetch_attempt_at"]


def test_an_already_fetched_source_is_not_fetched_again(worker_env, monkeypatch):
    source = _add(
        worker_env,
        _source(raw_text="본문", source_metadata={"fetch_state": "FETCHED"}),
    )

    async def never(**_kwargs):
        raise AssertionError("이미 본문이 있는 자료를 다시 받지 않는다")

    monkeypatch.setattr(tasks, "fetch_source_content", never)

    assert tasks.fetch_channel_source.apply(args=[str(source.id)]).get() == {
        "source_id": str(source.id),
        "status": "ALREADY_FETCHED",
    }


def test_the_sweep_redispatches_a_stalled_fetch_after_the_cooldown(worker_env):
    stale = datetime.now(timezone.utc) - timedelta(minutes=20)
    source = _add(
        worker_env,
        _source(
            source_metadata={
                "fetch_state": "FAILED",
                "fetch_attempts": 1,
                "last_fetch_attempt_at": stale.isoformat(),
            }
        ),
    )

    assert tasks._redispatch_stalled_channel_source_fetches() == 1
    assert worker_env.calls["dispatched"] == [[str(source.id)]]


def test_the_sweep_waits_out_the_cooldown(worker_env):
    recent = datetime.now(timezone.utc) - timedelta(minutes=2)
    _add(
        worker_env,
        _source(
            source_metadata={
                "fetch_state": "FAILED",
                "fetch_attempts": 1,
                "last_fetch_attempt_at": recent.isoformat(),
            }
        ),
    )

    assert tasks._redispatch_stalled_channel_source_fetches() == 0
    assert worker_env.calls["dispatched"] == []


def test_the_sweep_stops_at_the_budget_and_opens_one_incident(worker_env):
    source = _add(
        worker_env,
        _source(
            source_metadata={
                "fetch_state": "FAILED",
                "fetch_attempts": tasks.CHANNEL_FETCH_ATTEMPT_BUDGET,
                "fetch_error": "URL 크롤링 실패: 연결할 수 없습니다",
            }
        ),
    )

    assert tasks._redispatch_stalled_channel_source_fetches() == 0
    assert worker_env.calls["dispatched"] == []
    assert source.status == SourceStatus.ERROR
    assert source.process_error == "URL 크롤링 실패: 연결할 수 없습니다"
    assert len(worker_env.calls["opened"]) == 1
    assert worker_env.calls["opened"][0]["pipeline"] == "source_fetch"
    assert worker_env.calls["opened"][0]["next_action"] == tasks.SOURCE_FETCH_NEXT_ACTION
    assert source.source_metadata["incident_id"]


def test_the_sweep_ignores_rows_that_are_not_waiting_on_a_fetch(worker_env):
    _add(worker_env, _source(source_metadata={}))

    assert tasks._redispatch_stalled_channel_source_fetches() == 0
    assert worker_env.calls["dispatched"] == []
    assert worker_env.calls["opened"] == []


def test_a_processing_error_opens_an_incident_the_person_can_act_on(worker_env):
    source = _add(worker_env, _source(raw_text="본문"))

    incident_id = tasks._open_source_incident(
        pipeline=tasks.SOURCE_PROCESSING_PIPELINE,
        source_id=source.id,
        hospital_id=source.hospital_id,
        hospital_name="테스트의원",
        incident_type="SOURCE_PROCESSING_FAILED",
        safe_error_code="SOURCE_PROCESSING_FAILED",
        problem="자료 본문이 없는 URL 전용 자료는 처리할 수 없습니다.",
        next_action=tasks.SOURCE_PROCESSING_NEXT_ACTION,
    )

    assert incident_id is not None
    opened = worker_env.calls["opened"][0]
    assert opened["pipeline"] == "source_processing"
    assert opened["object_type"] == "source_asset"
    assert opened["next_action"] == "자료 내용을 확인하고 다시 올리거나 제외해 주세요."
    assert opened["admin_path"] == f"/hospitals/{source.hospital_id}/info"


def test_a_processed_source_closes_its_incident(worker_env):
    source_id = uuid.uuid4()

    tasks._recover_source_incident(
        pipeline=tasks.SOURCE_PROCESSING_PIPELINE,
        source_id=source_id,
        hospital_name="테스트의원",
    )

    assert worker_env.calls["recovered"][0]["object_id"] == str(source_id)
    assert worker_env.calls["recovered"][0]["pipeline"] == "source_processing"

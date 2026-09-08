"""채널 자료 fetch 워커와 그 복구 스윕(B1) — 그리고 처리 실패의 운영 예외(B2).

프로파일 저장은 본문 없는 PENDING 행만 만든다. 이 파일은 그 뒤를 검사한다: 워커가 본문을
받아 처리로 잇는지, 영구 실패를 굳히는지, 일시 실패를 예산 안에서만 다시 거는지, 예산을 다
쓴 자료가 사람의 할 일이 되는지.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from app.models.essence import SourceStatus, SourceType
from app.services.essence_sources import (
    FetchedSourceContent,
    SourceRegistrationError,
    TransientSourceFetchError,
)
from app.workers import tasks


def _rendered(clause) -> str:
    """실제로 DB에 나갈 SQL. 조건을 파이썬이 아니라 SQL이 거는지 여기서 드러난다."""
    return str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


class _FakeSession:
    """`SyncSessionLocal()` 대신 쓰는 최소 세션. 저장소는 테스트가 들고 있는 dict다.

    조회는 **렌더링된 SQL에 실제로 있는 조건만** 적용하고 LIMIT도 그대로 따른다.
    파이썬에서 한 번 더 거르면 "LIMIT 앞에서 걸렀는가"를 테스트가 증명하지 못한다.
    """

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def _sources(self):
        return [
            row for row in self.store.values() if getattr(row, "source_type", None) is not None
        ]

    def get(self, _model, object_id):
        return self.store.get(object_id)

    def execute(self, stmt):
        if isinstance(stmt, Update):
            return self._apply_update(stmt)
        sql = _rendered(stmt)
        rows = [row for row in self._sources() if self._matches(row, sql)]
        rows.sort(key=lambda row: row.created_at)
        limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
        if limit is not None:
            rows = rows[:limit]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    @staticmethod
    def _matches(row, sql: str) -> bool:
        metadata = row.source_metadata or {}
        checks = [
            ("status = 'PENDING'", row.status == SourceStatus.PENDING),
            ("status = 'ERROR'", row.status == SourceStatus.ERROR),
            ("raw_text IS NULL", row.raw_text is None),
            ("url IS NOT NULL", bool(row.url)),
            (
                "'fetch_state') AS VARCHAR) IN ('QUEUED', 'FAILED')",
                metadata.get("fetch_state") in ("QUEUED", "FAILED"),
            ),
            (
                "'fetch_state') AS VARCHAR) = 'FAILED'",
                metadata.get("fetch_state") == "FAILED",
            ),
            ("'incident_id') AS VARCHAR) IS NULL", metadata.get("incident_id") is None),
        ]
        return all(passed for fragment, passed in checks if fragment in sql)

    def _apply_update(self, stmt):
        sql = _rendered(stmt.whereclause)
        values = {
            column.name: getattr(bind, "value", bind) for column, bind in stmt._values.items()
        }
        rows = [
            row
            for row in self._sources()
            if f"'{row.id}'" in sql and self._matches(row, sql)
        ]
        for row in rows:
            for name, value in values.items():
                setattr(row, name, value)
        return SimpleNamespace(rowcount=len(rows))

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


def test_legacy_url_rows_cannot_starve_a_freshly_queued_channel(worker_env):
    """자동 처리 대상이 아닌 옛 URL 전용 행이 LIMIT 200칸을 채워도 새 등록은 발행된다.

    자격 조건을 LIMIT 뒤 파이썬에서 걸면, 오래된 레거시 행 250개가 후보를 다 차지해
    방금 프로파일 저장이 만든 QUEUED 행은 영원히 fetch되지 않는다.
    """
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for index in range(250):
        # `fetch_state`가 없는 행 — 계약상 자동 처리 대상이 아니다.
        _add(
            worker_env,
            _source(
                source_metadata={"registered_from": "legacy"},
                created_at=old + timedelta(seconds=index),
            ),
        )
    queued = _add(worker_env, _source(created_at=datetime(2026, 9, 9, tzinfo=timezone.utc)))

    assert tasks._redispatch_stalled_channel_source_fetches() == 1
    assert worker_env.calls["dispatched"] == [[str(queued.id)]]


def test_a_terminal_failure_whose_incident_cannot_open_stays_retryable(worker_env, monkeypatch):
    """사고를 열지 못하면 ERROR로 굳히지 않는다 — 굳히면 아무도 다시 보지 않는다."""
    source = _add(worker_env, _source(source_type=SourceType.NAVER_BLOG))

    async def shell_page(**_kwargs):
        raise SourceRegistrationError("네이버 블로그 본문을 가져오지 못했습니다.")

    async def incident_open_fails(**_kwargs):
        raise RuntimeError("incident store unavailable")

    monkeypatch.setattr(tasks, "fetch_source_content", shell_page)
    monkeypatch.setattr(tasks, "open_ops_incident", incident_open_fails)

    result = tasks.fetch_channel_source.apply(args=[str(source.id)]).get()

    assert result["status"] == "FAILED"
    assert source.status == SourceStatus.PENDING
    assert source.source_metadata["fetch_state"] == "FAILED"
    assert "incident_id" not in source.source_metadata
    # 스윕의 후보 조건 그대로다 — 쿨다운이 지나면 같은 종결을 다시 시도한다.
    source.source_metadata["last_fetch_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=20)
    ).isoformat()
    assert tasks._redispatch_stalled_channel_source_fetches() == 1


def test_the_sweep_reconciles_a_terminal_row_that_never_got_an_incident(worker_env):
    """사고 없이 ERROR로 굳은 행은 사고만 열어 사람이 볼 수 있게 되돌린다."""
    source = _add(
        worker_env,
        _source(
            status=SourceStatus.ERROR,
            process_error="URL 크롤링 실패: 연결할 수 없습니다",
            source_metadata={"fetch_state": "FAILED", "fetch_attempts": 3},
        ),
    )

    tasks._redispatch_stalled_channel_source_fetches()

    assert len(worker_env.calls["opened"]) == 1
    opened = worker_env.calls["opened"][0]
    assert opened["object_id"] == str(source.id)
    assert opened["problem"] == "URL 크롤링 실패: 연결할 수 없습니다"
    assert source.source_metadata["incident_id"]

    # 사고가 붙은 뒤에는 다시 열지 않는다.
    tasks._redispatch_stalled_channel_source_fetches()
    assert len(worker_env.calls["opened"]) == 1


def test_an_operator_exclusion_wins_over_a_late_terminal_error(worker_env, monkeypatch):
    """종결 쓰기는 status=PENDING을 다시 확인한다 — 제외 결정을 늦은 ERROR가 덮지 않는다."""
    source = _add(worker_env, _source(status=SourceStatus.EXCLUDED))

    tasks._fail_channel_source_fetch(
        source.id, message="영구 실패", terminal=True, incident_id=uuid.uuid4()
    )

    assert source.status == SourceStatus.EXCLUDED
    assert source.process_error is None
    # CAS가 막았으므로 fetch 흔적도 남기지 않는다.
    assert source.source_metadata["fetch_state"] == "QUEUED"


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

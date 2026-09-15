"""채널 자료 fetch 실패가 실제 Postgres에서 인시던트를 만들지 않고, 옛 인시던트는 걷힌다(B2).

단위 테스트는 인시던트 기록을 double로 바꾼다. 여기서는 진짜 Incident 행을 만들어, 예산을
다 쓴 자료가 인시던트 없이 ERROR로 굳는지와 정리 pass가 링크·상태를 수렴시키는지를
저장된 행으로 확인한다.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services.incident_types import (
    IncidentFingerprint,
    IncidentOpenRequest,
    IncidentSeverity,
)
from app.services.incidents import (
    acknowledge_incident,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.workers import channel_source_fetch_cleanup, tasks
from app.workers.channel_source_fetch_cleanup import (
    retire_channel_source_fetch_incidents,
)

_POSTGRES_URL = os.getenv(
    "OPERATIONS_TEST_DATABASE_URL",
    "postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test",
)


def _async_url(url: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def _source_row(hospital_id: uuid.UUID, **overrides) -> HospitalSourceAsset:
    values = dict(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        source_type=SourceType.HOMEPAGE,
        title="병원 홈페이지",
        url="https://clinic.invalid",
        raw_text=None,
        status=SourceStatus.PENDING,
        source_metadata={
            "channel_field": "website_url",
            "fetch_state": "FAILED",
            "fetch_attempts": tasks.CHANNEL_FETCH_ATTEMPT_BUDGET,
            "fetch_error": "URL 크롤링 실패: 연결할 수 없습니다",
        },
    )
    values.update(overrides)
    return HospitalSourceAsset(**values)


async def _open_incident(
    db: AsyncSession, *, hospital_id: uuid.UUID, object_id: str, incident_type: str
) -> Incident:
    incident = await open_or_touch_incident(
        db,
        IncidentOpenRequest(
            pipeline="source_fetch" if "FETCH" in incident_type else "source_processing",
            object_type="source_asset",
            object_id=object_id,
            fingerprint=IncidentFingerprint.VALIDATION_FAILED,
            incident_type=incident_type,
            severity=IncidentSeverity.MEDIUM,
            customer_impact="이 자료는 새 글의 근거로 쓰이지 않습니다.",
            source_type="HOSPITAL_SOURCE_ASSET",
            next_action="공식 채널 주소를 확인해 주세요.",
            admin_path=f"/hospitals/{hospital_id}/info",
            hospital_id=hospital_id,
            safe_error_code=incident_type,
        ),
    )
    await db.commit()
    return incident


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch):
    """실 Postgres 세션 한 쌍(sync/async)과 병원 하나. 만든 행은 끝에서 직접 지운다."""
    engine = create_engine(_POSTGRES_URL, future=True)
    try:
        engine.connect().close()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    session = Session(engine, expire_on_commit=False)
    async_engine = create_async_engine(_async_url(_POSTGRES_URL), poolclass=NullPool)
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", SessionContext)
    monkeypatch.setattr(
        tasks.fetch_channel_source, "apply_async", lambda args=None, **_kwargs: None
    )

    hospital_id = uuid.uuid4()
    session.add(
        Hospital(
            id=hospital_id, name="채널 자료 의원", slug=f"channel-fetch-{uuid.uuid4().hex}"
        )
    )
    session.commit()

    def run(coro_factory):
        async def _run():
            async with async_sessions() as db:
                return await coro_factory(db)

        return asyncio.run(_run())

    try:
        yield type(
            "PG", (), {"session": session, "hospital_id": hospital_id, "run": staticmethod(run)}
        )
    finally:
        session.rollback()
        session.execute(
            delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
        )
        session.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
        session.execute(
            delete(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id)
        )
        session.execute(delete(Hospital).where(Hospital.id == hospital_id))
        session.commit()
        session.close()
        engine.dispose()
        asyncio.run(async_engine.dispose())


def test_exhausted_channel_fetch_hardens_the_row_without_an_incident(pg) -> None:
    source = _source_row(pg.hospital_id)
    pg.session.add(source)
    pg.session.commit()

    assert tasks._redispatch_stalled_channel_source_fetches() == 0

    pg.session.expire_all()
    stored = pg.session.get(HospitalSourceAsset, source.id)
    assert stored.status == SourceStatus.ERROR
    assert stored.process_error == "URL 크롤링 실패: 연결할 수 없습니다"
    assert stored.source_metadata["fetch_error"] == "URL 크롤링 실패: 연결할 수 없습니다"
    assert "incident_id" not in stored.source_metadata
    assert (
        list(pg.session.scalars(select(Incident).where(Incident.hospital_id == pg.hospital_id)))
        == []
    )
    assert (
        list(
            pg.session.scalars(
                select(NotificationOutbox).where(
                    NotificationOutbox.hospital_id == pg.hospital_id
                )
            )
        )
        == []
    )


def test_the_cleanup_pass_retires_fetch_links_in_every_incident_state(pg) -> None:
    """OPEN·RECOVERED·ACKNOWLEDGED 모두 링크는 걷히고, 아직 열린 것만 RECOVERED가 된다."""
    sources: dict[str, HospitalSourceAsset] = {}
    for key in ("open", "recovered", "acknowledged", "processing"):
        source = _source_row(pg.hospital_id, status=SourceStatus.ERROR)
        sources[key] = source
        pg.session.add(source)
    pg.session.commit()

    incident_ids: dict[str, uuid.UUID] = {}
    for key, source in sources.items():
        incident_type = (
            "SOURCE_PROCESSING_FAILED" if key == "processing" else "CHANNEL_SOURCE_FETCH_FAILED"
        )
        incident = pg.run(
            lambda db, s=source, t=incident_type: _open_incident(
                db, hospital_id=pg.hospital_id, object_id=str(s.id), incident_type=t
            )
        )
        incident_ids[key] = incident.id
        source.source_metadata = {**source.source_metadata, "incident_id": str(incident.id)}
    pg.session.commit()

    async def _advance(db: AsyncSession) -> None:
        for key in ("recovered", "acknowledged"):
            incident = await db.get(Incident, incident_ids[key])
            retrying = await mark_retrying(
                db, incident.id, expected_version=incident.version, actor="test", reason="setup"
            )
            recovered = await mark_recovered(
                db,
                retrying.id,
                expected_version=retrying.version,
                observed_success=True,
                actor="test",
                reason="setup",
            )
            if key == "acknowledged":
                await acknowledge_incident(
                    db,
                    recovered.id,
                    expected_version=recovered.version,
                    acknowledged_by_id=None,
                    actor="test",
                    reason="setup",
                )
        await db.commit()

    pg.run(_advance)

    result = pg.run(lambda db: retire_channel_source_fetch_incidents(db))

    assert result == {"links_retired": 3, "incidents_recovered": 1}

    pg.session.expire_all()
    for key in ("open", "recovered", "acknowledged"):
        metadata = pg.session.get(HospitalSourceAsset, sources[key].id).source_metadata
        assert "incident_id" not in metadata
        assert metadata["incident_retired_at"]
        # 자료 행의 사실은 그대로다 — 사람은 이 표에서 원인을 읽는다.
        assert metadata["fetch_error"] == "URL 크롤링 실패: 연결할 수 없습니다"

    processing_metadata = pg.session.get(
        HospitalSourceAsset, sources["processing"].id
    ).source_metadata
    assert processing_metadata["incident_id"] == str(incident_ids["processing"])
    assert "incident_retired_at" not in processing_metadata

    states = {
        key: pg.session.get(Incident, incident_ids[key]).state for key in incident_ids
    }
    assert states["open"] == IncidentState.RECOVERED.value
    assert states["recovered"] == IncidentState.RECOVERED.value
    assert states["acknowledged"] == IncidentState.ACKNOWLEDGED.value
    assert states["processing"] == IncidentState.OPEN.value

    # 알릴 새 사실이 없다 — 정리는 Slack·outbox를 만들지 않는다.
    assert (
        list(
            pg.session.scalars(
                select(NotificationOutbox).where(
                    NotificationOutbox.hospital_id == pg.hospital_id
                )
            )
        )
        == []
    )

    # 두 번째 pass는 할 일이 없다(멱등·수렴).
    assert pg.run(lambda db: retire_channel_source_fetch_incidents(db)) == {
        "links_retired": 0,
        "incidents_recovered": 0,
    }

    # 스윕이 다시 돌아도 걷어 낸 링크를 되살리지 않는다.
    tasks._redispatch_stalled_channel_source_fetches()
    pg.session.expire_all()
    assert "incident_id" not in pg.session.get(
        HospitalSourceAsset, sources["open"].id
    ).source_metadata


def test_the_cleanup_pass_respects_its_per_pass_cap(pg) -> None:
    sources = [_source_row(pg.hospital_id, status=SourceStatus.ERROR) for _ in range(3)]
    for source in sources:
        pg.session.add(source)
    pg.session.commit()
    for source in sources:
        incident = pg.run(
            lambda db, s=source: _open_incident(
                db,
                hospital_id=pg.hospital_id,
                object_id=str(s.id),
                incident_type="CHANNEL_SOURCE_FETCH_FAILED",
            )
        )
        source.source_metadata = {**source.source_metadata, "incident_id": str(incident.id)}
    pg.session.commit()

    def _pass(limit: int) -> int:
        return pg.run(
            lambda db: retire_channel_source_fetch_incidents(db, limit=limit)
        )["links_retired"]

    assert _pass(2) == 2
    assert _pass(2) == 1
    assert _pass(2) == 0


def test_the_cleanup_pass_closes_a_fetch_incident_no_source_row_points_at(pg) -> None:
    """링크가 없는(또는 먼저 걷힌) 열린 fetch 인시던트도 남겨 두지 않는다."""
    orphan = pg.run(
        lambda db: _open_incident(
            db,
            hospital_id=pg.hospital_id,
            object_id=str(uuid.uuid4()),
            incident_type="CHANNEL_SOURCE_FETCH_FAILED",
        )
    )
    processing = pg.run(
        lambda db: _open_incident(
            db,
            hospital_id=pg.hospital_id,
            object_id=str(uuid.uuid4()),
            incident_type="SOURCE_PROCESSING_FAILED",
        )
    )

    assert pg.run(lambda db: retire_channel_source_fetch_incidents(db)) == {
        "links_retired": 0,
        "incidents_recovered": 1,
    }

    pg.session.expire_all()
    assert pg.session.get(Incident, orphan.id).state == IncidentState.RECOVERED.value
    assert pg.session.get(Incident, processing.id).state == IncidentState.OPEN.value


def test_a_recovery_conflict_keeps_the_link_until_the_next_pass(
    pg, monkeypatch: pytest.MonkeyPatch
) -> None:
    """닫지 못한 인시던트의 링크를 떼면 발견 경로가 사라진다 — 다음 pass까지 남긴다."""
    source = _source_row(pg.hospital_id, status=SourceStatus.ERROR)
    pg.session.add(source)
    pg.session.commit()
    incident = pg.run(
        lambda db: _open_incident(
            db,
            hospital_id=pg.hospital_id,
            object_id=str(source.id),
            incident_type="CHANNEL_SOURCE_FETCH_FAILED",
        )
    )
    source.source_metadata = {**source.source_metadata, "incident_id": str(incident.id)}
    pg.session.commit()

    async def conflicted(*_args, **_kwargs):
        return "someone else moved this row"

    monkeypatch.setattr(channel_source_fetch_cleanup, "mark_recovered", conflicted)

    assert pg.run(lambda db: retire_channel_source_fetch_incidents(db)) == {
        "links_retired": 0,
        "incidents_recovered": 0,
    }

    pg.session.expire_all()
    stored = pg.session.get(HospitalSourceAsset, source.id)
    assert stored.source_metadata["incident_id"] == str(incident.id)
    assert "incident_retired_at" not in stored.source_metadata

    monkeypatch.undo()

    assert pg.run(lambda db: retire_channel_source_fetch_incidents(db)) == {
        "links_retired": 1,
        "incidents_recovered": 1,
    }
    pg.session.expire_all()
    stored = pg.session.get(HospitalSourceAsset, source.id)
    assert "incident_id" not in stored.source_metadata
    assert stored.source_metadata["incident_retired_at"]
    assert pg.session.get(Incident, incident.id).state == IncidentState.RECOVERED.value


def test_a_concurrent_metadata_write_survives_the_link_retirement(pg) -> None:
    """읽고-고쳐-쓰기였다면 지워졌을 동시 쓰기(처리 링크·fetch 계수)가 그대로 남는다."""
    source = _source_row(pg.hospital_id, status=SourceStatus.ERROR)
    pg.session.add(source)
    pg.session.commit()
    fetch_incident = pg.run(
        lambda db: _open_incident(
            db,
            hospital_id=pg.hospital_id,
            object_id=str(source.id),
            incident_type="CHANNEL_SOURCE_FETCH_FAILED",
        )
    )
    source.source_metadata = {**source.source_metadata, "incident_id": str(fetch_incident.id)}
    pg.session.commit()

    async def _retire_after_a_concurrent_write(db: AsyncSession):
        # 정리 pass가 행을 읽은 뒤, 쓰기 직전에 다른 쓰기가 커밋된다.
        await db.execute(
            select(HospitalSourceAsset).where(HospitalSourceAsset.id == source.id)
        )
        source.source_metadata = {
            **source.source_metadata,
            "processing_incident_id": "keep-me",
            "fetch_attempts": 9,
        }
        pg.session.commit()
        return await retire_channel_source_fetch_incidents(db)

    assert pg.run(_retire_after_a_concurrent_write)["links_retired"] == 1

    pg.session.expire_all()
    metadata = pg.session.get(HospitalSourceAsset, source.id).source_metadata
    assert "incident_id" not in metadata
    assert metadata["incident_retired_at"]
    # 동시 쓰기가 남긴 사실은 하나도 잃지 않는다.
    assert metadata["processing_incident_id"] == "keep-me"
    assert metadata["fetch_attempts"] == 9
    assert metadata["fetch_error"] == "URL 크롤링 실패: 연결할 수 없습니다"


def test_the_retirement_statement_only_touches_the_observed_link() -> None:
    """컴파일된 SQL이 읽고-고쳐-쓰기가 아니라 조건부 부분 갱신인지 문장으로 확인한다."""
    sql = " ".join(str(channel_source_fetch_cleanup.RETIRE_SOURCE_LINK_SQL).split())

    assert "source_metadata - 'incident_id'" in sql
    assert "jsonb_build_object('incident_retired_at', CAST(:retired_at AS text))" in sql
    # 관측한 값이 그대로일 때만 쓴다 — 다른 쪽이 먼저 바꿨으면 0행이다.
    assert "source_metadata->>'incident_id' = :incident_id" in sql

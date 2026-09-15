"""fetch 예산을 다 쓴 채널 자료가 실제 Postgres에서 사람의 할 일이 된다(B1-c).

단위 테스트는 예외 기록을 double로 바꾼다. 여기서는 진짜 `open_ops_incident`를 통과시켜,
dedupe 키·admin 경로·다시 열지 않음까지 저장된 행으로 확인한다.
"""

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
from app.models.operations import Incident, NotificationOutbox
from app.services import ops_incident_alerts
from app.workers import tasks

_POSTGRES_URL = os.getenv(
    "OPERATIONS_TEST_DATABASE_URL",
    "postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test",
)


def _async_url(url: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def test_exhausted_channel_fetch_opens_exactly_one_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(_POSTGRES_URL, future=True)
    try:
        engine.connect().close()
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")
    session = Session(engine, expire_on_commit=False)
    async_engine = create_async_engine(_async_url(_POSTGRES_URL), poolclass=NullPool)
    async_sessions = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: async_sessions)

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(tasks, "SyncSessionLocal", SessionContext)
    dispatched: list[list[str]] = []
    monkeypatch.setattr(
        tasks.fetch_channel_source,
        "apply_async",
        lambda args=None, **_kwargs: dispatched.append(list(args or [])),
    )

    hospital_id = uuid.uuid4()
    source_id = uuid.uuid4()
    try:
        session.add(
            Hospital(
                id=hospital_id,
                name="채널 자료 예외 의원",
                slug=f"channel-fetch-{uuid.uuid4().hex}",
            )
        )
        session.add(
            HospitalSourceAsset(
                id=source_id,
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
        )
        session.commit()

        assert tasks._redispatch_stalled_channel_source_fetches() == 0
        assert dispatched == []

        session.expire_all()
        source = session.get(HospitalSourceAsset, source_id)
        incidents = list(
            session.scalars(select(Incident).where(Incident.hospital_id == hospital_id))
        )

        assert source.status == SourceStatus.ERROR
        assert source.process_error == "URL 크롤링 실패: 연결할 수 없습니다"
        assert len(incidents) == 1
        incident = incidents[0]
        assert incident.state == "OPEN"
        assert incident.safe_error_code == "CHANNEL_SOURCE_FETCH_FAILED"
        assert incident.next_action == tasks.SOURCE_FETCH_NEXT_ACTION
        assert incident.admin_path == f"/hospitals/{hospital_id}/info"
        assert str(source.source_metadata["incident_id"]) == str(incident.id)

        # 자료 id로 dedupe한다 — 스윕이 다시 돌아도 두 번째 예외는 열리지 않는다.
        source.status = SourceStatus.PENDING
        session.commit()
        assert tasks._redispatch_stalled_channel_source_fetches() == 0
        session.expire_all()
        assert (
            len(list(session.scalars(select(Incident).where(Incident.hospital_id == hospital_id))))
            == 1
        )
    finally:
        # 이 테스트는 롤백되는 트랜잭션 밖에서 커밋한다 — 만든 행은 직접 지운다.
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

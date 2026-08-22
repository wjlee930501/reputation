"""V0 세션 advisory 락은 오류 경로에서도 반드시 풀려야 한다 — 실제 Postgres로 확인한다.

배경: `trigger_v0_report`는 ANALYZING 커밋과 MeasurementRun RUNNING 커밋 사이를
`pg_advisory_lock`(세션 락)으로 감싼다. 세션 락은 트랜잭션 롤백으로 풀리지 않고
커넥션에 붙어 있으므로, 두 가지 방식으로 영구 잔류할 수 있다:

1. 락 구간에서 SQLAlchemy 오류가 나면 세션이 pending-rollback 상태라 unlock 쿼리
   자체가 실패한다.
2. 기본 Session은 commit/rollback마다 커넥션을 풀에 반환하므로, 풀에 커넥션이 둘
   이상이면 그 다음 문장(=unlock)이 다른 커넥션에서 돈다. unlock은 예외 없이
   false를 반환하고 락은 원래 커넥션에 남는다.

어느 쪽이든 같은 병원의 프로파일 저장·자료 업로드·V0 재시도가 알림 없이 무한
대기한다. 두 경로 모두 실제 SQL로 재현해 고정한다.
"""
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.utils.db_locks import (
    acquire_hospital_advisory_session_lock_sync,
    hospital_lock_key,
    release_hospital_advisory_session_lock_sync,
)
from app.workers.tasks import release_v0_session_lock


@pytest.fixture
def pooled_engine(pg_engine):
    """워커 sync 엔진과 같은 모양의 풀 — 커넥션이 여러 개 있는 상태를 재현한다."""
    engine = create_engine(pg_engine.url, pool_size=2, max_overflow=2, future=True)
    try:
        # 풀에 커넥션 2개를 채워 둔다. 하나뿐이면 commit 후 항상 같은 커넥션이 돌아와
        # 락이 커넥션을 옮겨 다니는 실패를 관찰할 수 없다.
        warmed = [engine.connect() for _ in range(2)]
        for connection in warmed:
            connection.close()
        yield engine
    finally:
        engine.dispose()


def _lock_is_free(engine, hospital_id: uuid.UUID) -> bool:
    """제3의 커넥션에서 같은 키를 잡아 본다 — 잔류 락이 있으면 잡히지 않는다."""
    key = hospital_lock_key(hospital_id)
    with engine.connect() as probe:
        acquired = probe.execute(select(func.pg_try_advisory_lock(key))).scalar()
        if acquired:
            probe.execute(select(func.pg_advisory_unlock(key)))
        return bool(acquired)


def test_error_inside_the_lock_region_still_releases_the_session_lock(pooled_engine):
    hospital_id = uuid.uuid4()
    connection = pooled_engine.connect()
    try:
        with Session(bind=connection, expire_on_commit=False) as db:
            acquire_hospital_advisory_session_lock_sync(db, hospital_id)
            db.commit()
            with pytest.raises(SQLAlchemyError):
                db.execute(text("SELECT 1 / 0"))

            release_v0_session_lock(db, hospital_id)

            assert _lock_is_free(pooled_engine, hospital_id)
    finally:
        connection.close()


def test_the_lock_survives_the_intermediate_commit_and_is_released_after_it(pooled_engine):
    """ANALYZING 커밋 이후에도 락이 유지되고, 마지막에 같은 커넥션에서 풀려야 한다."""
    hospital_id = uuid.uuid4()
    connection = pooled_engine.connect()
    try:
        with Session(bind=connection, expire_on_commit=False) as db:
            acquire_hospital_advisory_session_lock_sync(db, hospital_id)
            db.commit()

            # 락 구간 한가운데 — 다른 커넥션은 이 병원 키를 잡을 수 없어야 한다.
            assert not _lock_is_free(pooled_engine, hospital_id)

            release_v0_session_lock(db, hospital_id)

            assert _lock_is_free(pooled_engine, hospital_id)
    finally:
        connection.close()


def test_an_unpinned_session_loses_the_lock_to_the_pool_on_commit(pooled_engine):
    """커넥션을 고정하지 않으면 unlock이 다른 커넥션에서 돌아 해제에 실패한다.

    `SyncSessionPinnedConnection`이 왜 필요한지를 고정하는 테스트다. rollback 한 줄만
    추가하는 수정으로는 이 경로를 막을 수 없다 — 락을 들고 있는 커넥션에 더 이상
    닿을 수 없기 때문이다.
    """
    hospital_id = uuid.uuid4()
    with Session(bind=pooled_engine, expire_on_commit=False) as db:
        acquire_hospital_advisory_session_lock_sync(db, hospital_id)
        db.commit()  # 커넥션이 풀로 반환된다 — 다음 문장은 다른 커넥션에서 돈다.

        released = release_hospital_advisory_session_lock_sync(db, hospital_id)

        # Postgres는 그 커넥션이 락을 들고 있지 않으면 예외 없이 false를 준다 —
        # 락은 풀 안의 다른 커넥션에 그대로 남는다.
        assert released is False

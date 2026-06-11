"""병원 단위 Postgres advisory lock — 키 유도/획득의 단일 진실 (R5).

workers/tasks.py와 services/exposure_action_engine.py가 동일한 uuid→int64 키 산식을
각자 복사해 쓰던 것을 한 곳으로 모은다. 산식이 한쪽만 바뀌면 두 경로가 서로 다른
락을 잡아 직렬화가 조용히 깨지기 때문.
"""
import uuid

from sqlalchemy import func, select


def hospital_lock_key(hospital_id: uuid.UUID) -> int:
    """uuid 128bit → signed 64bit 키로 축약 (충돌해도 과잉 직렬화일 뿐 정합성엔 무해)."""
    lock_key = (hospital_id.int ^ (hospital_id.int >> 64)) & 0xFFFFFFFFFFFFFFFF
    if lock_key >= 2**63:
        lock_key -= 2**64
    return lock_key


def _is_postgres_bind(db) -> bool:
    """Postgres가 아닌 바인딩(단위 테스트의 fake/SQLite)에서는 advisory lock을 생략."""
    try:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    except Exception:
        return False
    return dialect_name == "postgresql"


def acquire_hospital_advisory_lock_sync(db, hospital_id: uuid.UUID) -> None:
    """pg_advisory_xact_lock — 트랜잭션 종료(commit/rollback) 시 자동 해제 (sync 세션용)."""
    if not _is_postgres_bind(db):
        return
    db.execute(select(func.pg_advisory_xact_lock(hospital_lock_key(hospital_id))))


async def acquire_hospital_advisory_lock(db, hospital_id: uuid.UUID) -> None:
    """pg_advisory_xact_lock — 트랜잭션 종료(commit/rollback) 시 자동 해제 (async 세션용)."""
    if not _is_postgres_bind(db):
        return
    await db.execute(select(func.pg_advisory_xact_lock(hospital_lock_key(hospital_id))))

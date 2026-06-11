"""R5 — 병원 advisory lock 키 유도 단일화."""
import uuid

from app.utils.db_locks import (
    acquire_hospital_advisory_lock_sync,
    hospital_lock_key,
)


def test_hospital_lock_key_is_signed_64bit_and_deterministic():
    hospital_id = uuid.UUID("9b2f8c54-1c3a-4e7d-8a16-5f0d2b9e6c41")

    key = hospital_lock_key(hospital_id)

    assert -(2**63) <= key < 2**63
    assert key == hospital_lock_key(hospital_id)
    # 기존 tasks.py / exposure_action_engine.py 산식과 동일해야 한다 (락 호환성).
    legacy = (hospital_id.int ^ (hospital_id.int >> 64)) & 0xFFFFFFFFFFFFFFFF
    if legacy >= 2**63:
        legacy -= 2**64
    assert key == legacy


def test_acquire_sync_is_noop_on_non_postgres_bind():
    class _FakeDB:
        def __init__(self):
            self.executed = []

        def get_bind(self):
            return type("Bind", (), {"dialect": type("D", (), {"name": "sqlite"})()})()

        def execute(self, stmt):
            self.executed.append(stmt)

    db = _FakeDB()
    acquire_hospital_advisory_lock_sync(db, uuid.uuid4())
    assert db.executed == []


def test_acquire_sync_is_noop_when_bind_unavailable():
    class _NoBindDB:
        def get_bind(self):
            raise RuntimeError("no bind in unit-test fake")

    # 예외 없이 조용히 no-op이어야 한다
    acquire_hospital_advisory_lock_sync(_NoBindDB(), uuid.uuid4())

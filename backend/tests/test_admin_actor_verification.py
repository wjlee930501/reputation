"""#5 — X-Admin-Actor 헤더 위조 방지: 활성 AdminUser.email과 매칭될 때만 채택."""
from types import SimpleNamespace

import pytest

from app.core import security


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, matched=None, raises=False, rollback_raises=False):
        self._matched = matched
        self._raises = raises
        self._rollback_raises = rollback_raises
        self.executed = 0
        self.rolled_back = 0

    async def execute(self, _stmt):
        self.executed += 1
        if self._raises:
            raise RuntimeError("db unavailable")
        return _Result(self._matched)

    async def rollback(self):
        self.rolled_back += 1
        if self._rollback_raises:
            raise RuntimeError("rollback failed")


async def test_missing_header_returns_none_and_skips_db():
    db = _FakeDB()
    assert await security._resolve_admin_actor(db, None) is None
    assert await security._resolve_admin_actor(db, "   ") is None
    assert db.executed == 0  # 헤더 없으면 DB 조회하지 않는다


async def test_non_email_format_labeled_unverified_without_db():
    db = _FakeDB()
    actor = await security._resolve_admin_actor(db, "김민지 AE")
    assert actor == "unverified:김민지 AE"
    assert db.executed == 0


async def test_matching_active_admin_email_is_accepted():
    db = _FakeDB(matched="owner@example.com")
    actor = await security._resolve_admin_actor(db, " Owner@Example.com ")
    assert actor == "owner@example.com"
    assert db.executed == 1


async def test_unknown_email_labeled_unverified():
    db = _FakeDB(matched=None)
    actor = await security._resolve_admin_actor(db, "ghost@attacker.com")
    assert actor == "unverified:ghost@attacker.com"


async def test_db_failure_does_not_trust_header():
    db = _FakeDB(raises=True)
    actor = await security._resolve_admin_actor(db, "owner@example.com")
    assert actor == "unverified:owner@example.com"
    # 조회 실패 시 공유 세션을 롤백해 이후 쿼리의 PendingRollbackError 500을 막는다.
    assert db.rolled_back == 1


async def test_db_failure_rollback_error_is_defended():
    """롤백 자체가 실패해도 actor 판정은 unverified로 안전하게 끝난다(예외 전파 금지)."""
    db = _FakeDB(raises=True, rollback_raises=True)
    actor = await security._resolve_admin_actor(db, "owner@example.com")
    assert actor == "unverified:owner@example.com"
    assert db.rolled_back == 1


async def test_capture_admin_actor_sets_and_resets_context():
    """정상 매칭 시 default_actor가 검증된 이메일을 반환하고, 종료 후 원복된다."""
    from app.services.audit_log import default_actor

    db = _FakeDB(matched="owner@example.com")
    request = SimpleNamespace(headers=SimpleNamespace(get=lambda *_a, **_k: "owner@example.com"))

    gen = security.capture_admin_actor(request, db=db)
    await gen.__anext__()
    try:
        assert default_actor() == "owner@example.com"
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
    # 컨텍스트 원복 후에는 헤더 값이 남지 않는다.
    assert default_actor() != "owner@example.com"

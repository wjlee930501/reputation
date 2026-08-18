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


def _request(method: str, actor: str | None, path: str = "/api/v1/admin/leads"):
    headers = {"X-Admin-Actor": actor} if actor is not None else {}
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=SimpleNamespace(get=lambda key, default=None: headers.get(key, default)),
    )


async def _drain(gen):
    await gen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.fixture(autouse=True)
def _reset_alert_throttle():
    security._unverified_alert_sent_at.clear()
    yield
    security._unverified_alert_sent_at.clear()


async def test_unverified_write_is_rejected_when_option_enabled(monkeypatch):
    """비활성/미매칭 계정 헤더로 온 쓰기 요청은 옵션을 켜면 백엔드에서 끊긴다."""
    monkeypatch.setattr(security.settings, "ADMIN_REJECT_UNVERIFIED_ACTOR", True)
    db = _FakeDB(matched=None)

    gen = security.capture_admin_actor(_request("POST", "ghost@attacker.com"), db=db)
    with pytest.raises(security.HTTPException) as exc:
        await gen.__anext__()
    assert exc.value.status_code == 403


async def test_unverified_read_is_never_rejected(monkeypatch):
    """읽기 요청은 옵션을 켜도 차단하지 않는다 — 감사 로그로 드러내는 것으로 충분하다."""
    monkeypatch.setattr(security.settings, "ADMIN_REJECT_UNVERIFIED_ACTOR", True)
    db = _FakeDB(matched=None)

    await _drain(security.capture_admin_actor(_request("GET", "ghost@attacker.com"), db=db))


async def test_actorless_system_write_is_never_rejected(monkeypatch):
    """헤더 없이 오는 배치/시스템 쓰기(default_actor 폴백)는 옵션과 무관하게 통과해야 한다."""
    monkeypatch.setattr(security.settings, "ADMIN_REJECT_UNVERIFIED_ACTOR", True)
    db = _FakeDB(matched=None)

    await _drain(security.capture_admin_actor(_request("POST", None), db=db))
    assert db.executed == 0


async def test_unverified_write_logs_and_alerts_when_option_disabled(monkeypatch, caplog):
    """기본 설정(거부 안 함)에서도 특권 쓰기 시도는 로그와 Slack 경보로 드러나야 한다."""
    monkeypatch.setattr(security.settings, "ADMIN_REJECT_UNVERIFIED_ACTOR", False)
    alerts = []

    async def fake_alert(**payload):
        alerts.append(payload)
        return None

    from app.services import ops_incident_alerts

    monkeypatch.setattr(ops_incident_alerts, "open_ops_incident", fake_alert)

    db = _FakeDB(matched=None)
    with caplog.at_level("WARNING", logger="app.core.security"):
        await _drain(security.capture_admin_actor(_request("DELETE", "ghost@attacker.com"), db=db))

    assert "admin actor not verified" in caplog.text
    assert "ghost@attacker.com" in caplog.text
    # 경보는 요청을 블로킹하지 않도록 백그라운드 태스크로 나간다.
    for task in list(security._pending_alert_tasks):
        await task
    assert alerts and alerts[0]["object_id"] == "unverified:ghost@attacker.com"
    assert alerts[0]["safe_error_code"] == "UNVERIFIED_ADMIN_ACTOR"


def test_unverified_alert_is_throttled_per_actor():
    """위조 헤더 반복 전송으로 Slack이 flood되지 않도록 actor별 창 안에서는 1회만 경보한다."""
    actor = "unverified:ghost@attacker.com"

    assert security._should_alert_unverified(actor, now=1000.0) is True
    assert security._should_alert_unverified(actor, now=1000.0 + 599.0) is False
    assert security._should_alert_unverified(actor, now=1000.0 + 601.0) is True
    # 다른 actor는 서로의 억제 창에 영향을 받지 않는다.
    assert security._should_alert_unverified("unverified:other@x.com", now=1000.0) is True


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

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


# ── H-10: 사람 변경은 BFF가 서명한 actor 단언을 요구한다 ────────────────────


def _sign_assertion(secret: str, payload: dict, *, mangle: bool = False) -> str:
    """admin/lib/actor-assertion.ts와 같은 규칙으로 단언을 만든다."""
    import base64
    import hashlib
    import hmac
    import json

    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode().rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), f"v1.{encoded}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if mangle:
        signature = ("0" if signature[0] != "0" else "1") + signature[1:]
    return f"v1.{encoded}.{signature}"


def _assertion_payload(*, email: str = "owner@example.com", ttl_ms: int = 120_000) -> dict:
    import time as _time

    issued_at = int(_time.time() * 1000)
    return {
        "email": email,
        "role": "OWNER",
        "iat": issued_at,
        "exp": issued_at + ttl_ms,
        "nonce": "0123456789abcdef0123456789abcdef",
    }


def _assertion_request(method: str, headers: dict[str, str], path: str = "/api/v1/admin/leads"):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=SimpleNamespace(get=lambda key, default=None: headers.get(key, default)),
    )


async def _actor_for(request, db) -> str:
    from app.services.audit_log import default_actor

    gen = security.capture_admin_actor(request, db=db)
    await gen.__anext__()
    try:
        return default_actor()
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


@pytest.fixture
def _actor_secret(monkeypatch):
    monkeypatch.setattr(security.settings, "BFF_ACTOR_SECRET", "test-actor-secret")
    return "test-actor-secret"


async def test_write_without_assertion_is_rejected_with_operator_guidance(_actor_secret):
    """(a) 배포 직후 옛 JS를 띄워둔 탭이 여기 걸린다 — 스스로 복구할 문구가 있어야 한다."""
    db = _FakeDB(matched="owner@example.com")
    gen = security.capture_admin_actor(_assertion_request("POST", {}), db=db)

    with pytest.raises(security.HTTPException) as exc:
        await gen.__anext__()

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ACTOR_ASSERTION_REQUIRED"
    assert exc.value.detail["message"] == (
        "관리 화면을 새로고침한 뒤 다시 시도해 주세요(로그인 세션 갱신 필요)."
    )
    assert db.executed == 0  # 단언 없이는 DB를 건드리지 않는다


async def test_valid_assertion_adopts_the_signed_email_as_actor(_actor_secret):
    """(b) 서명된 이메일을 actor로 채택하되 활성 계정 매칭은 그대로 적용한다.

    Admin BFF는 같은 세션 이메일로 `X-Admin-Actor`도 함께 보낸다(대소문자만 다를 수 있다)
    — 정상 브라우저 트래픽이 불일치 규칙에 걸리지 않아야 한다.
    """
    db = _FakeDB(matched="owner@example.com")
    token = _sign_assertion(_actor_secret, _assertion_payload())

    actor = await _actor_for(
        _assertion_request(
            "POST", {"X-Admin-Actor-Assertion": token, "X-Admin-Actor": "Owner@Example.com"}
        ),
        db,
    )

    assert actor == "owner@example.com"
    assert db.executed == 1


async def test_expired_assertion_is_rejected(_actor_secret):
    """(c) 120초 TTL이 재생 창을 제한한다 — 만료 뒤에는 같은 토큰이 통하지 않는다."""
    db = _FakeDB(matched="owner@example.com")
    token = _sign_assertion(_actor_secret, _assertion_payload(ttl_ms=-1))

    gen = security.capture_admin_actor(
        _assertion_request("POST", {"X-Admin-Actor-Assertion": token}), db=db
    )
    with pytest.raises(security.HTTPException) as exc:
        await gen.__anext__()

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ACTOR_ASSERTION_INVALID"


async def test_assertion_signed_with_another_secret_is_rejected(_actor_secret):
    """(d) 다른 키로 서명했거나 서명을 손댄 단언은 통과하지 못한다."""
    db = _FakeDB(matched="owner@example.com")
    for token in (
        _sign_assertion("other-secret", _assertion_payload()),
        _sign_assertion(_actor_secret, _assertion_payload(), mangle=True),
        "v1.not-base64.deadbeef",
        "garbage",
    ):
        gen = security.capture_admin_actor(
            _assertion_request("PATCH", {"X-Admin-Actor-Assertion": token}), db=db
        )
        with pytest.raises(security.HTTPException) as exc:
            await gen.__anext__()
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "ACTOR_ASSERTION_INVALID"


async def test_read_without_assertion_is_allowed(_actor_secret):
    """(e) 읽기는 단언 없이 통과한다."""
    db = _FakeDB(matched="owner@example.com")

    actor = await _actor_for(_assertion_request("GET", {"X-Admin-Actor": "owner@example.com"}), db)

    assert actor == "owner@example.com"


async def test_system_header_write_is_allowed_and_recorded_as_system_actor(_actor_secret):
    """(f) 배치/CLI는 세션이 없다 — 시스템 헤더로 통과하되 actor에 job 이름을 남긴다."""
    db = _FakeDB(matched=None)

    actor = await _actor_for(_assertion_request("POST", {"X-Admin-Actor-System": "nightly"}), db)

    assert actor == "system:nightly"
    assert db.executed == 0  # 시스템 호출에는 매칭할 AdminUser가 없다


async def test_malformed_system_job_name_is_rejected(_actor_secret):
    db = _FakeDB(matched=None)
    gen = security.capture_admin_actor(
        _assertion_request("DELETE", {"X-Admin-Actor-System": "night ly\n"}), db=db
    )
    with pytest.raises(security.HTTPException) as exc:
        await gen.__anext__()
    assert exc.value.detail["code"] == "ACTOR_ASSERTION_INVALID"


async def test_assertion_is_not_enforced_without_a_configured_secret():
    """시크릿이 없으면 검증 자체가 불가능하다 — 프로덕션 부팅은 config가 막는다."""
    db = _FakeDB(matched="owner@example.com")

    actor = await _actor_for(_assertion_request("POST", {"X-Admin-Actor": "owner@example.com"}), db)

    assert actor == "owner@example.com"


# ── Astra B2: 확인된 actor만이 인가·감사의 권위다 ──────────────────────────


async def test_unsigned_actor_header_that_differs_from_the_assertion_is_rejected(_actor_secret):
    """OPERATOR의 정상 단언에 OWNER 평문 헤더를 얹어 권한을 올릴 수 없다."""
    db = _FakeDB(matched="operator@example.com")
    token = _sign_assertion(_actor_secret, _assertion_payload(email="operator@example.com"))

    gen = security.capture_admin_actor(
        _assertion_request(
            "POST",
            {"X-Admin-Actor-Assertion": token, "X-Admin-Actor": "owner@example.com"},
        ),
        db=db,
    )
    with pytest.raises(security.HTTPException) as exc:
        await gen.__anext__()

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ACTOR_ASSERTION_MISMATCH"
    assert db.executed == 0


async def test_system_call_never_adopts_an_accompanying_actor_header(_actor_secret):
    """시스템 헤더 + OWNER 평문 헤더 → actor는 job 이름이고, OWNER는 감사에 남지 않는다."""
    db = _FakeDB(matched="owner@example.com")

    request = _assertion_request(
        "POST", {"X-Admin-Actor-System": "cli", "X-Admin-Actor": "owner@example.com"}
    )
    effective = security.resolve_request_actor(request)
    actor = await _actor_for(request, db)

    assert actor == "system:cli"
    assert effective.system_job == "cli"
    assert effective.email is None
    # 사칭된 값은 기록용으로만 남고 인가·감사 어디에도 채택되지 않는다.
    assert effective.claimed_actor == "owner@example.com"
    assert effective.audit_actor == "system:cli"
    assert db.executed == 0


def test_resolve_request_actor_keeps_header_behaviour_without_a_secret():
    """시크릿이 없는 로컬/테스트 환경은 종전대로 X-Admin-Actor를 그대로 쓴다."""
    effective = security.resolve_request_actor(
        _assertion_request("POST", {"X-Admin-Actor": "owner@example.com"})
    )
    assert effective.email == "owner@example.com"
    assert effective.system_job is None
    assert effective.verified is False


def test_resolve_request_actor_keeps_header_behaviour_on_reads(_actor_secret):
    """읽기는 단언을 강제하지 않으므로 헤더 불일치도 거부 대상이 아니다."""
    effective = security.resolve_request_actor(
        _assertion_request("GET", {"X-Admin-Actor": "owner@example.com"})
    )
    assert effective.email == "owner@example.com"
    assert effective.verified is False

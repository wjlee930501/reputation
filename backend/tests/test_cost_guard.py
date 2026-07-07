"""cost_guard 서비스 + operations 비용 가드 엔드포인트 테스트.

fakeredis 패키지가 미설치이므로, 기존 test_admin_session_revocation.py의 FakeRedis
패턴을 확장해 이 테스트에서 필요한 async 메서드(get/set/incrby/expire/exists/delete)를
구현한다.
"""
import pytest
from redis.exceptions import RedisError

from app.api.admin import operations as operations_api
from app.models.content import ContentType
from app.schemas.operations import CostGuardKillSwitchRequest
from app.services import cost_guard
from app.services import audit_log


class FakeRedis:
    def __init__(self):
        self.store: dict[str, object] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False

    async def _guard(self):
        if self.fail:
            raise RedisError("redis down")

    async def get(self, key):
        await self._guard()
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        await self._guard()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def incrby(self, key, amount):
        await self._guard()
        new = int(self.store.get(key, 0)) + amount
        self.store[key] = new
        return new

    async def expire(self, key, ttl):
        await self._guard()
        self.ttls[key] = ttl
        return True

    async def exists(self, key):
        await self._guard()
        return 1 if key in self.store else 0

    async def delete(self, key):
        await self._guard()
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1


class AlertRecorder:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, *, title, message):
        self.calls.append({"title": title, "message": message})
        return True


@pytest.fixture
def alerts(monkeypatch):
    recorder = AlertRecorder()
    monkeypatch.setattr(cost_guard.notifier, "notify_ops_alert", recorder)
    return recorder


def _set_limits(monkeypatch, *, category="content", daily, monthly):
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    field = {
        "content": ("COST_GUARD_DAILY_CONTENT_CALLS", "COST_GUARD_MONTHLY_CONTENT_CALLS"),
        "image": ("COST_GUARD_DAILY_IMAGE_CALLS", "COST_GUARD_MONTHLY_IMAGE_CALLS"),
        "sov": ("COST_GUARD_DAILY_SOV_QUERIES", "COST_GUARD_MONTHLY_SOV_QUERIES"),
    }[category]
    monkeypatch.setattr(cost_guard.settings, field[0], daily)
    monkeypatch.setattr(cost_guard.settings, field[1], monthly)


async def test_allows_and_increments_counter(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=100, monthly=100)
    redis = FakeRedis()

    decision = await cost_guard.check_and_increment("content", redis_client=redis)

    assert decision.allowed is True
    assert decision.reason is None
    # 일/월 카운터 둘 다 1 증가 + TTL 세팅
    daily_keys = [k for k in redis.store if ":daily:" in k]
    monthly_keys = [k for k in redis.store if ":monthly:" in k]
    assert redis.store[daily_keys[0]] == 1
    assert redis.store[monthly_keys[0]] == 1
    assert redis.ttls[daily_keys[0]] == cost_guard._DAILY_TTL_SECONDS
    assert redis.ttls[monthly_keys[0]] == cost_guard._MONTHLY_TTL_SECONDS
    assert alerts.calls == []


async def test_blocks_when_monthly_hard_cap_reached(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=1000, monthly=3)
    redis = FakeRedis()

    results = [
        await cost_guard.check_and_increment("content", redis_client=redis)
        for _ in range(4)
    ]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert "월간" in results[-1].reason
    # 차단된 호출은 카운터를 증가시키지 않는다 (월간 카운터는 상한 3에 고정)
    monthly_key = next(k for k in redis.store if ":monthly:" in k)
    assert redis.store[monthly_key] == 3
    # 하드 알림은 1회만
    hard = [c for c in alerts.calls if "상한 도달" in c["title"]]
    assert len(hard) == 1


async def test_blocks_when_daily_hard_cap_reached(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=2, monthly=1000)
    redis = FakeRedis()

    results = [
        await cost_guard.check_and_increment("content", redis_client=redis)
        for _ in range(3)
    ]

    assert [r.allowed for r in results] == [True, True, False]
    assert "일일" in results[-1].reason


async def test_soft_warning_fires_once(monkeypatch, alerts):
    # monthly=10 → soft threshold = 8. daily 높게 둬 간섭 방지.
    _set_limits(monkeypatch, daily=1000, monthly=10)
    redis = FakeRedis()

    for _ in range(9):  # 8번째에 소프트, 9번째는 dedup
        await cost_guard.check_and_increment("content", redis_client=redis)

    soft = [c for c in alerts.calls if "소프트 경고" in c["title"]]
    assert len(soft) == 1


async def test_hard_warning_fires_once_on_reaching_limit(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=1000, monthly=3)
    redis = FakeRedis()

    for _ in range(5):  # 3번째 도달 시 hard, 이후 차단은 dedup
        await cost_guard.check_and_increment("content", redis_client=redis)

    hard = [c for c in alerts.calls if "상한 도달" in c["title"]]
    assert len(hard) == 1


async def test_kill_switch_blocks_all_categories(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=1000, monthly=1000)
    redis = FakeRedis()
    await cost_guard.set_kill_switch(True, redis_client=redis)

    for category in ("content", "image", "sov"):
        decision = await cost_guard.check_and_increment(category, redis_client=redis)
        assert decision.allowed is False
        assert "킬스위치" in decision.reason

    # 킬스위치 해제 후 다시 허용
    await cost_guard.set_kill_switch(False, redis_client=redis)
    assert (await cost_guard.check_and_increment("content", redis_client=redis)).allowed is True


async def test_redis_failure_fails_open(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=1, monthly=1)
    redis = FakeRedis()
    redis.fail = True

    decision = await cost_guard.check_and_increment("content", redis_client=redis)

    # 가용성 우선: Redis 장애 시 생성은 계속(allowed=True)
    assert decision.allowed is True
    assert decision.reason is None


async def test_disabled_returns_allowed_without_redis(monkeypatch):
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", False)

    class ExplodingRedis:
        async def exists(self, *a, **k):
            raise AssertionError("disabled guard must not touch redis")

    decision = await cost_guard.check_and_increment("content", redis_client=ExplodingRedis())
    assert decision.allowed is True


async def test_batch_count_increments_by_count(monkeypatch, alerts):
    _set_limits(monkeypatch, category="sov", daily=1000, monthly=1000)
    redis = FakeRedis()

    await cost_guard.check_and_increment("sov", count=5, redis_client=redis)

    monthly_key = next(k for k in redis.store if ":monthly:" in k)
    assert redis.store[monthly_key] == 5


async def test_unknown_category_raises(monkeypatch):
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    with pytest.raises(ValueError):
        await cost_guard.check_and_increment("bogus", redis_client=FakeRedis())


async def test_get_usage_snapshot(monkeypatch):
    _set_limits(monkeypatch, category="content", daily=100, monthly=200)
    _set_limits(monkeypatch, category="image", daily=10, monthly=20)
    _set_limits(monkeypatch, category="sov", daily=5, monthly=50)
    redis = FakeRedis()
    await cost_guard.check_and_increment("content", redis_client=redis)
    await cost_guard.set_kill_switch(True, redis_client=redis)

    snapshot = await cost_guard.get_usage_snapshot(redis_client=redis)

    assert snapshot["enabled"] is True
    assert snapshot["kill_switch_active"] is True
    by_cat = {c["category"]: c for c in snapshot["categories"]}
    assert set(by_cat) == {"content", "image", "sov"}
    assert by_cat["content"]["monthly_used"] == 1
    assert by_cat["content"]["monthly_limit"] == 200
    assert by_cat["image"]["monthly_used"] == 0


async def test_snapshot_degrades_gracefully_on_redis_failure(monkeypatch):
    _set_limits(monkeypatch, category="content", daily=100, monthly=200)
    redis = FakeRedis()
    redis.fail = True

    snapshot = await cost_guard.get_usage_snapshot(redis_client=redis)

    # 500 대신 상한만이라도 반환(사용량 0)
    assert snapshot["kill_switch_active"] is False
    assert len(snapshot["categories"]) == 3


# ── generation 스킵 경로 (이미지 생성 차단 시 이미지 없이 진행) ──────────────
async def test_image_generation_skipped_when_cost_guard_blocks(monkeypatch):
    from app.services import image_engine

    async def _blocked(category, **kwargs):
        return cost_guard.CostGuardDecision(False, "차단")

    monkeypatch.setattr(cost_guard, "check_and_increment", _blocked)

    result = await image_engine.generate_image(ContentType.FAQ, "clinic-slug", topic="무릎 통증")

    # 이미지 없이 ("", "") — 본문 파이프라인은 기존 실패 경로로 계속 진행
    assert result == ("", "")


# ── operations 엔드포인트 ────────────────────────────────────────────────
class FakeDB:
    def __init__(self):
        self.events = []

    def add(self, item):
        self.events.append(("add", item))

    async def commit(self):
        self.events.append(("commit", None))

    @property
    def added(self):
        return [item for kind, item in self.events if kind == "add"]

    @property
    def committed(self):
        return any(kind == "commit" for kind, _ in self.events)


async def test_get_cost_guard_status_endpoint(monkeypatch):
    _set_limits(monkeypatch, category="content", daily=100, monthly=200)
    _set_limits(monkeypatch, category="image", daily=10, monthly=20)
    _set_limits(monkeypatch, category="sov", daily=5, monthly=50)
    redis = FakeRedis()
    monkeypatch.setattr(cost_guard, "_client", lambda: redis)

    response = await operations_api.get_cost_guard_status()

    # 엔드포인트는 dict를 반환하고 FastAPI가 response_model로 직렬화한다.
    assert response["enabled"] is True
    assert response["kill_switch_active"] is False
    assert {c["category"] for c in response["categories"]} == {"content", "image", "sov"}


async def test_kill_switch_endpoint_audits_then_commits_then_sets(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(cost_guard, "_client", lambda: redis)
    monkeypatch.setattr(audit_log.settings, "ADMIN_ACTOR_NAME", "AE-test")
    db = FakeDB()

    # set 호출 시점의 db 이벤트 순서를 포착해 audit→commit→side-effect 순서를 검증
    order_at_set = {}
    original_set = cost_guard.set_kill_switch

    async def _tracking_set(enabled, **kwargs):
        order_at_set["events"] = list(db.events)
        return await original_set(enabled, redis_client=redis)

    monkeypatch.setattr(cost_guard, "set_kill_switch", _tracking_set)

    response = await operations_api.set_cost_guard_kill_switch(
        CostGuardKillSwitchRequest(enabled=True), db=db
    )

    assert response.kill_switch_active is True
    assert db.added[0].action == "cost_guard_kill_switch"
    assert db.added[0].actor == "AE-test"
    assert db.added[0].detail == {"enabled": True}
    # side-effect(set_kill_switch) 전에 add + commit 이 이미 일어났는지
    events = order_at_set["events"]
    assert events[0][0] == "add"
    assert events[1] == ("commit", None)
    assert await cost_guard._is_kill_switch_active(redis) is True


async def test_kill_switch_endpoint_disable(monkeypatch):
    redis = FakeRedis()
    await cost_guard.set_kill_switch(True, redis_client=redis)
    monkeypatch.setattr(cost_guard, "_client", lambda: redis)

    db = FakeDB()
    response = await operations_api.set_cost_guard_kill_switch(
        CostGuardKillSwitchRequest(enabled=False), db=db
    )

    assert response.kill_switch_active is False
    assert await cost_guard._is_kill_switch_active(redis) is False

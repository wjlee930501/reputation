"""비용 가드 fail-open 사고를 실제 PostgreSQL·Redis로 확인한다.

- 실패가 이어지면 5분마다 관측을 다시 기록해 11분이 지나도 근거 정리가 닫지 않는다.
- Redis가 PING에 응답해도 예산 연산이 실패하면(키 타입 오류) 닫지 않는다.
- 알린 프로세스가 교체되면 실제 예산 연산이 성공한 뒤에만 근거 정리가 닫는다.
- 근거 정리가 닫은 뒤 같은 프로세스가 계속 실패하면 메모리 상태와 무관하게 다시 연다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services import cost_guard, cost_guard_health
from app.services.incident_types import IncidentFingerprint
from app.services.incidents import build_incident_key
from app.workers import incident_backlog
from tests.db_env import require_db_url, require_redis_url

_KEY = build_incident_key("cost_guard", "fail_open", "content", IncidentFingerprint.BROKER_UNAVAILABLE)


def test_budget_probe_needs_the_real_operation_not_just_a_ping() -> None:
    url = require_redis_url("COST_GUARD_REDIS_URL")
    client = redis.Redis.from_url(url)
    # 지난 기간의 키를 써서 오늘 카운터를 건드리지 않는다.
    at = datetime(2026, 1, 15, 12, tzinfo=cost_guard._KST)
    daily = cost_guard._daily_key("content", cost_guard._daily_period(at))
    monthly = cost_guard._monthly_key("content", cost_guard._monthly_period(at))
    try:
        client.delete(daily, monthly)
        assert cost_guard.probe_budget_operation("content", redis_url=url, now=at) is True
        # 0건 예약이라 카운터는 그대로다.
        assert int(client.get(daily) or 0) == 0
        # 키 타입이 깨지면 PING은 되지만 예산 연산은 실패한다 — 복구 근거가 아니다.
        client.delete(daily)
        client.hset(daily, "broken", "1")
        assert client.ping() is True
        assert cost_guard.probe_budget_operation("content", redis_url=url, now=at) is False
    finally:
        client.delete(daily, monthly)
        client.close()


def _probe_keys(at: datetime) -> tuple[str, str, str]:
    return (
        cost_guard._daily_key("content", cost_guard._daily_period(at)),
        cost_guard._monthly_key("content", cost_guard._monthly_period(at)),
        cost_guard._daily_override_key("content", cost_guard._daily_period(at)),
    )


def test_budget_probe_applies_the_override_and_reaches_the_receipt_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기본 100·상향 200·사용 150: 탐침은 실제 예약과 같은 한도(200)로 쓰기 경로까지 간다.

    지출은 그대로이고 탐침 영수증은 남지 않는다. HSET이 막힌 사용자로는 실패한다 — 한도에 걸려
    쓰기 전에 돌아오던 옛 탐침은 이 경우를 성공으로 보고했다.
    """
    url = require_redis_url("COST_GUARD_REDIS_URL")
    monkeypatch.setattr(cost_guard, "_limits", lambda _c: (100, 0))
    client = redis.Redis.from_url(url)
    at = datetime(2026, 1, 16, 12, tzinfo=cost_guard._KST)
    daily, monthly, override = _probe_keys(at)
    user = "cost_guard_probe_no_hset"
    seen: list[tuple] = []
    real_eval = redis.Redis.eval

    def spy_eval(self, script, numkeys, *args):
        seen.append(args[numkeys:])
        return real_eval(self, script, numkeys, *args)

    monkeypatch.setattr(redis.Redis, "eval", spy_eval)
    try:
        client.delete(daily, monthly, override)
        client.set(daily, 150, ex=500)
        client.set(override, 200)
        assert cost_guard.probe_budget_operation("content", redis_url=url, now=at) is True
        assert seen[-1][0] == 200  # 실제 예약과 같은 상향 적용 한도
        # 지출은 늘지 않고, TTL도 다시 걸리지 않으며, 없던 월간 키를 만들지 않는다.
        assert int(client.get(daily)) == 150
        assert 0 < client.ttl(daily) <= 500
        assert client.exists(monthly) == 0
        assert list(client.scan_iter("cost_guard:reservation:probe:*")) == []

        client.execute_command("ACL", "SETUSER", user, "reset", "on", ">probe-pw", "~*", "+@all", "-hset")
        host = url.split("://", 1)[1]
        denied = f"redis://{user}:probe-pw@{host}"
        assert cost_guard.probe_budget_operation("content", redis_url=denied, now=at) is False
        assert int(client.get(daily)) == 150
    finally:
        client.execute_command("ACL", "DELUSER", user)
        client.delete(daily, monthly, override)
        client.close()


@pytest.mark.asyncio
async def test_fail_open_incident_lifecycle_against_the_real_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_engine = create_async_engine(require_db_url("INCIDENT_TEST_DATABASE_URL"))
    async_sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    sync_engine = create_engine(require_db_url("OPERATIONS_TEST_DATABASE_URL"))
    monkeypatch.setattr(cost_guard_health, "get_async_sessionmaker", lambda: async_sessions)
    # 근거 정리는 이 종류만 본다(공유 테스트 DB의 다른 사고를 닫지 않게).
    monkeypatch.setattr(
        incident_backlog,
        "RESOLVERS",
        {"COST_GUARD_FAIL_OPEN": incident_backlog.RESOLVERS["COST_GUARD_FAIL_OPEN"]},
    )
    budget_op = [False]
    monkeypatch.setattr(incident_backlog, "_cost_guard_budget_op_succeeds", lambda _c: budget_op[0])
    clock = [10_000.0]
    monkeypatch.setattr(cost_guard_health, "_monotonic", lambda: clock[0])

    async def cleanup() -> None:
        async with async_sessions() as db:
            ids = select(Incident.id).where(Incident.dedupe_key == _KEY)
            await db.execute(delete(NotificationOutbox).where(NotificationOutbox.incident_id.in_(ids)))
            await db.execute(delete(Incident).where(Incident.dedupe_key == _KEY))
            await db.commit()

    async def incident() -> Incident:
        async with async_sessions() as db:
            row = await db.scalar(select(Incident).where(Incident.dedupe_key == _KEY))
            assert row is not None
            return row

    async def age_last_seen(minutes: int) -> None:
        async with async_sessions() as db:
            await db.execute(
                update(Incident)
                .where(Incident.dedupe_key == _KEY)
                .values(last_seen_at=datetime.now(UTC) - timedelta(minutes=minutes))
            )
            await db.commit()

    def run_backlog(now: datetime) -> int:
        with Session(sync_engine) as db:
            closed = incident_backlog.close_resolved_backlog_incidents(db, now=now)
            db.commit()
            return closed

    cost_guard_health.reset_for_tests()
    await cleanup()
    try:
        await cost_guard_health.report_fail_open("content", "ResponseError")
        first = await incident()
        assert first.state == IncidentState.OPEN.value

        # 11분째에도 같은 프로세스가 실패 중이다. 첫 기록은 11분 전이고, 5분마다 다시 기록한다.
        await age_last_seen(11)
        clock[0] += 301
        await cost_guard_health.report_fail_open("content", "ResponseError")
        touched = await incident()
        assert touched.last_seen_at > datetime.now(UTC) - timedelta(minutes=1)
        # 예산 연산이 된다 해도 마지막 관측 10분 안이면 닫지 않는다.
        budget_op[0] = True
        assert run_backlog(datetime.now(UTC)) == 0
        assert (await incident()).state == IncidentState.OPEN.value

        # 관측이 멈춘 지 11분 — PING은 되지만 예산 연산이 계속 실패하면 닫지 않는다.
        await age_last_seen(11)
        budget_op[0] = False
        assert run_backlog(datetime.now(UTC)) == 0
        assert (await incident()).state == IncidentState.OPEN.value

        # 알린 프로세스가 교체됐다(메모리 상태 유실). 실제 예산 연산이 성공하면 닫는다.
        cost_guard_health.reset_for_tests()
        budget_op[0] = True
        assert run_backlog(datetime.now(UTC)) == 1
        closed = await incident()
        assert closed.state == IncidentState.ACKNOWLEDGED.value

        # 알렸던 다른 프로세스는 여전히 실패 중이다 — 메모리 상태가 다시 열기를 막지 않는다.
        cost_guard_health._REPORTED.add("content")
        cost_guard_health._ATTEMPTED_AT["content"] = clock[0]
        clock[0] += 301
        await cost_guard_health.report_fail_open("content", "ResponseError")
        reopened = await incident()
        assert reopened.state == IncidentState.OPEN.value
        assert reopened.episode_seq == closed.episode_seq + 1
    finally:
        cost_guard_health.reset_for_tests()
        await cleanup()
        await async_engine.dispose()
        sync_engine.dispose()


def test_incident_key_matches_the_health_module() -> None:
    request = cost_guard_health._request("content", "x")
    assert (
        build_incident_key(request.pipeline, request.object_type, request.object_id, request.fingerprint)
        == _KEY
    )

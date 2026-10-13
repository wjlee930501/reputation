"""외부 감시 메일 기록의 정본이 DB(감사행)임을 실제 PostgreSQL로 확인한다.

Redis가 내려가 있으면(메일을 보내는 바로 그때일 수 있다) 에피소드도 메일 기록도 Redis에서 읽을 수 없다.
같은 장애가 KST 자정을 넘겨도 한 키·한 본문으로 한 통이어야 하고, 프로세스가 바뀌어도(메모리 유실)
DB가 이미 보낸 메일을 기억해야 한다. 감사행은 append-only라 지우지 않는다 — 조건 이름에 매번 새 값을 써
실행마다 다른 키를 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.audit import AdminAuditLog
from app.services import pipeline_watchdog
from tests.db_env import require_db_url


class _DownRedis:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _key):
        raise RedisError("redis down")

    def set(self, *_args, **_kwargs):
        raise RedisError("redis down")


@pytest.mark.asyncio
async def test_redis_down_outage_mails_once_and_the_db_remembers_across_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(require_db_url("OPERATIONS_TEST_DATABASE_URL"), poolclass=NullPool)
    monkeypatch.setattr(pipeline_watchdog, "_watchdog_engine", engine)
    monkeypatch.setattr(pipeline_watchdog, "_connect_redis", lambda: _DownRedis())
    pipeline_watchdog.reset_email_memory_for_tests()
    calls: list[dict] = []
    results = [
        pipeline_watchdog.ops_alert_email.OpsAlertResult.UNKNOWN,
        pipeline_watchdog.ops_alert_email.OpsAlertResult.SENT,
    ]

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return results.pop(0) if results else pipeline_watchdog.ops_alert_email.OpsAlertResult.SENT

    monkeypatch.setattr(pipeline_watchdog.ops_alert_email, "send_ops_alert", fake_send)
    conditions = (f"queue_stale:test-{uuid.uuid4().hex[:12]}",)

    def alert(text: str):
        return pipeline_watchdog.AlertDecision(
            True, "ALERT", "developer", text, "redis_unavailable",
            "https://hooks.slack.com/ops", conditions, None,
        )

    # 감사행의 created_at은 DB 시각이라 판정 시각도 지금을 쓴다(KST 자정 경계는 단위 테스트가 본다).
    now = datetime.now(pipeline_watchdog.KST)
    try:
        first = await pipeline_watchdog.email_undelivered((alert("대기열 정지 1개"),), (False,), now=now)
        second = await pipeline_watchdog.email_undelivered(
            (alert("대기열 정지 2개"),), (False,), now=now + timedelta(minutes=20)
        )
        # 새 프로세스(메모리 유실) — DB가 보낸 사실을 기억한다.
        pipeline_watchdog.reset_email_memory_for_tests()
        third = await pipeline_watchdog.email_undelivered(
            (alert("대기열 정지 3개"),), (False,), now=now + timedelta(minutes=35)
        )

        assert (first, second, third) == ((False,), (True,), (False,))
        keys = {call["idempotency_key"] for call in calls}
        assert len(keys) == 1
        assert calls[0]["lines"] == calls[1]["lines"]
        (key,) = keys
        with Session(engine) as db:
            actions = sorted(
                db.scalars(
                    select(AdminAuditLog.action).where(
                        AdminAuditLog.target_type == "watchdog_alert_email",
                        AdminAuditLog.target_id == key,
                    )
                )
            )
        assert actions == ["watchdog_ops_email_planned", "watchdog_ops_email_sent"]
    finally:
        pipeline_watchdog.reset_email_memory_for_tests()
        engine.dispose()

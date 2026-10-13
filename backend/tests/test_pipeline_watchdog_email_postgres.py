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


@pytest.mark.asyncio
async def test_the_first_mail_payload_is_kept_in_the_db_and_links_across_redis_outages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """에피소드의 첫 메일이 결과 불명이면 조건 집합이 줄어도 DB 계획의 제목·본문을 그대로 다시 보낸다.

    보낸 뒤 Redis가 내려가 에피소드 없는 판정이 와도(JSON 서명 조회) 같은 장애로 보고 다시 보내지 않는다.
    """
    engine = create_engine(require_db_url("OPERATIONS_TEST_DATABASE_URL"), poolclass=NullPool)
    monkeypatch.setattr(pipeline_watchdog, "_watchdog_engine", engine)
    redis_up = [True]

    class _UpRedis(_DownRedis):
        def get(self, _key):
            return None

        def set(self, *_args, **_kwargs):
            return True

    monkeypatch.setattr(
        pipeline_watchdog, "_connect_redis", lambda: _UpRedis() if redis_up[0] else _DownRedis()
    )
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
    tag = uuid.uuid4().hex[:12]
    first_set = (f"queue_stale:a-{tag}", f"queue_stale:b-{tag}")
    episode = f"ep-{tag}"

    def alert(conditions, episode_id):
        return pipeline_watchdog.AlertDecision(
            True, "ALERT", "developer", "인프라", "new_condition_set",
            "https://hooks.slack.com/ops", conditions, episode_id,
        )

    now = datetime.now(pipeline_watchdog.KST)
    try:
        first = await pipeline_watchdog.email_undelivered((alert(first_set, episode),), (False,), now=now)
        pipeline_watchdog.reset_email_memory_for_tests()
        second = await pipeline_watchdog.email_undelivered(
            (alert(first_set[:1], episode),), (False,), now=now + timedelta(minutes=15)
        )
        redis_up[0] = False
        pipeline_watchdog.reset_email_memory_for_tests()
        third = await pipeline_watchdog.email_undelivered(
            (alert(first_set, None),), (False,), now=now + timedelta(minutes=30)
        )

        assert (first, second, third) == ((False,), (True,), (False,))
        assert len(calls) == 2
        assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
        assert (calls[0]["title"], calls[0]["lines"]) == (calls[1]["title"], calls[1]["lines"])
        assert any(f"b-{tag}" in line for line in calls[1]["lines"])
    finally:
        pipeline_watchdog.reset_email_memory_for_tests()
        engine.dispose()

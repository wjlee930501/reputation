"""Admin 감시 엔드포인트 — 전용 토큰 또는 admin 키, Slack은 outbox를 거치지 않는다."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.api.admin import watchdog
from app.services.pipeline_watchdog import AlertDecision, WatchdogReport


def _report(**overrides) -> WatchdogReport:
    base = dict(
        observed_at=datetime(2026, 9, 12, 23, 40, tzinfo=UTC),
        kst_date="2026-09-13",
        redis_available=True,
        database_available=True,
        queue_canaries_current=False,
        stale_queues=("content",),
        stale_critical_queues=("content",),
        beat_alive=False,
        beat_lock_held=False,
        beat_last_schedule_run_at=None,
        beat_evidence="예약 실행기의 분산 락이 없다.",
        publish_checked=True,
        publish_due_remaining=9,
        publish_published_today=0,
        publish_missing=True,
        publish_partial=False,
        last_generation_batch_at=None,
        generation_batch_stale=True,
    )
    base.update(overrides)
    return WatchdogReport(**base)


async def test_watchdog_token_is_accepted_when_configured(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "PIPELINE_WATCHDOG_TOKEN", "  token-value  ")

    assert await watchdog.verify_watchdog_access(token="token-value", key=None) == "watchdog-token"


async def test_admin_key_still_works_without_the_watchdog_token(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "PIPELINE_WATCHDOG_TOKEN", "token-value")
    monkeypatch.setattr(watchdog.settings, "ADMIN_SECRET_KEY", "test-admin-key")

    assert await watchdog.verify_watchdog_access(token=None, key="test-admin-key") == (
        "test-admin-key"
    )


async def test_empty_setting_rejects_every_token(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "PIPELINE_WATCHDOG_TOKEN", "")
    monkeypatch.setattr(watchdog.settings, "ADMIN_SECRET_KEY", "test-admin-key")

    for candidate in ("", "   ", "anything"):
        with pytest.raises(HTTPException) as exc:
            await watchdog.verify_watchdog_access(token=candidate, key=None)
        assert exc.value.status_code == 401


async def test_wrong_token_without_admin_key_is_rejected(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "PIPELINE_WATCHDOG_TOKEN", "token-value")
    monkeypatch.setattr(watchdog.settings, "ADMIN_SECRET_KEY", "test-admin-key")

    with pytest.raises(HTTPException) as exc:
        await watchdog.verify_watchdog_access(token="token-valuX", key=None)
    assert exc.value.status_code == 401


async def test_read_endpoint_returns_the_report_as_json(monkeypatch):
    monkeypatch.setattr(watchdog, "_evaluate_now", lambda: _report())
    monkeypatch.setattr(watchdog.settings, "PIPELINE_WATCHDOG_TOKEN", "token-value")

    payload = await watchdog.read_pipeline_watchdog()

    assert payload["beat_alive"] is False
    assert payload["publish_missing"] is True
    assert payload["critical_conditions"] == [
        "beat_down",
        "publish_missing",
        "queue_stale:content",
    ]
    assert payload["healthy"] is False
    assert payload["token_configured"] is True


async def test_read_endpoint_reports_a_missing_token_instead_of_failing(monkeypatch):
    # 토큰이 없어도 API는 뜬다(부팅 차단 아님). 감시가 실제로 꺼져 있다는 사실만 드러낸다.
    monkeypatch.setattr(watchdog, "_evaluate_now", lambda: _report())
    monkeypatch.setattr(watchdog.settings, "PIPELINE_WATCHDOG_TOKEN", "   ")

    payload = await watchdog.read_pipeline_watchdog()

    assert payload["token_configured"] is False


async def test_alert_endpoint_sends_one_message_per_audience(monkeypatch):
    decisions = (
        AlertDecision(
            True, "ALERT", "developer", "인프라", "new_condition_set", "https://hooks.slack.com/dev"
        ),
        AlertDecision(
            True, "ALERT", "operator", "발행", "new_condition_set", "https://hooks.slack.com/ops"
        ),
    )
    delivered: list[AlertDecision] = []

    async def fake_deliver_all(values):
        delivered.extend(values)
        return tuple(True for _ in values)

    monkeypatch.setattr(watchdog, "_evaluate_and_decide", lambda: (_report(), decisions))
    monkeypatch.setattr(watchdog.pipeline_watchdog, "deliver_all", fake_deliver_all)

    payload = await watchdog.run_pipeline_watchdog_alert()

    assert list(delivered) == list(decisions)
    assert payload["alerts"] == [
        {
            "audience": "developer",
            "kind": "ALERT",
            "sent": True,
            "delivered": True,
            "reason": "new_condition_set",
        },
        {
            "audience": "operator",
            "kind": "ALERT",
            "sent": True,
            "delivered": True,
            "reason": "new_condition_set",
        },
    ]
    assert payload["report"]["kst_date"] == "2026-09-13"


async def test_alert_endpoint_stays_silent_when_healthy(monkeypatch):
    healthy = _report(
        queue_canaries_current=True,
        stale_queues=(),
        stale_critical_queues=(),
        beat_alive=True,
        beat_lock_held=True,
        publish_missing=False,
        publish_published_today=9,
        publish_due_remaining=0,
        generation_batch_stale=False,
    )
    decisions = (
        AlertDecision(False, None, "developer", None, "healthy", None),
        AlertDecision(False, None, "operator", None, "healthy", None),
    )

    async def fake_deliver_all(values):
        return tuple(False for _ in values)

    monkeypatch.setattr(watchdog, "_evaluate_and_decide", lambda: (healthy, decisions))
    monkeypatch.setattr(watchdog.pipeline_watchdog, "deliver_all", fake_deliver_all)

    payload = await watchdog.run_pipeline_watchdog_alert()

    assert [entry["sent"] for entry in payload["alerts"]] == [False, False]
    assert [entry["delivered"] for entry in payload["alerts"]] == [False, False]
    assert payload["report"]["healthy"] is True


def test_router_is_mounted_outside_the_shared_admin_key_dependency():
    import app.main as main

    assert main.admin_watchdog.router is watchdog.router
    assert watchdog.router.prefix == "/admin/watchdog"
    paths = {route.path for route in watchdog.router.routes}
    assert paths == {"/admin/watchdog/pipeline", "/admin/watchdog/pipeline/alert"}

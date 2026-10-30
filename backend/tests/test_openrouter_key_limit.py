from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import openai
import pytest

from app.models.content import ContentType
from app.services import image_engine, openrouter_key_limit
from app.workers import tasks

_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def _permission(message: str) -> openai.PermissionDeniedError:
    return openai.PermissionDeniedError(
        message,
        response=httpx.Response(403, request=_REQUEST),
        body={"error": {"message": message}},
    )


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key, value, *, nx, ex):
        assert nx is True
        assert ex > 0
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def exists(self, key):
        return int(key in self.values)

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)


def test_detects_only_the_specific_openrouter_permission_error():
    exact = _permission("Key limit exceeded (monthly limit)")
    other_403 = _permission("User not allowed to access this model")

    assert openrouter_key_limit.is_openrouter_key_limit_error(exact) is True
    assert openrouter_key_limit.is_openrouter_key_limit_error(other_403) is False


def test_detects_the_images_endpoint_http_403():
    response = httpx.Response(403, request=_REQUEST, text='{"error":"Key limit exceeded"}')
    error = httpx.HTTPStatusError(
        "openrouter images 403: Key limit exceeded",
        request=_REQUEST,
        response=response,
    )

    assert openrouter_key_limit.is_openrouter_key_limit_error(error) is True


def test_detects_key_limit_through_a_domain_wrapper():
    try:
        try:
            raise _permission("Key limit exceeded")
        except openai.PermissionDeniedError as exc:
            raise RuntimeError("image policy review failed") from exc
    except RuntimeError as wrapped:
        assert openrouter_key_limit.is_openrouter_key_limit_error(wrapped) is True


def test_circuit_is_key_and_month_scoped(monkeypatch):
    client = _Redis()
    october = datetime(2026, 10, 30, tzinfo=UTC)
    november = datetime(2026, 11, 1, tzinfo=UTC)
    monkeypatch.setattr(openrouter_key_limit.settings, "OPENROUTER_API_KEY", "key-a")

    assert openrouter_key_limit.trip_circuit(now=october, redis_client=client) is True
    assert openrouter_key_limit.trip_circuit(now=october, redis_client=client) is False
    assert openrouter_key_limit.circuit_is_open(now=october, redis_client=client) is True
    assert openrouter_key_limit.circuit_is_open(now=november, redis_client=client) is False

    monkeypatch.setattr(openrouter_key_limit.settings, "OPENROUTER_API_KEY", "key-b")
    assert openrouter_key_limit.circuit_is_open(now=october, redis_client=client) is False


def test_successful_probe_can_close_exact_key_month_circuit(monkeypatch):
    client = _Redis()
    october = datetime(2026, 10, 30, tzinfo=UTC)
    monkeypatch.setattr(openrouter_key_limit.settings, "OPENROUTER_API_KEY", "key-a")
    fingerprint = openrouter_key_limit.key_fingerprint()
    assert openrouter_key_limit.trip_circuit(now=october, redis_client=client) is True

    assert openrouter_key_limit.close_circuit(
        redis_client=client, fingerprint=fingerprint, period="2026-10"
    ) is True
    assert openrouter_key_limit.circuit_is_open(
        redis_client=client, fingerprint=fingerprint, period="2026-10"
    ) is False


def test_attempt_period_rolls_at_utc_month_boundary():
    october = datetime(2026, 10, 31, 23, 59, tzinfo=UTC)
    november = datetime(2026, 11, 1, tzinfo=UTC)

    assert openrouter_key_limit.attempt_period_is_current("2026-10", october) is True
    assert openrouter_key_limit.attempt_period_is_current("2026-10", november) is False


def test_generation_classifier_elects_only_the_first_limit_detector(monkeypatch):
    error = _permission("Key limit exceeded (monthly limit)")
    monkeypatch.setattr(openrouter_key_limit, "trip_circuit", lambda: False)

    code, message, notification_owner = tasks._classify_generation_exception(error)

    assert code == "OPENROUTER_KEY_LIMIT_EXCEEDED"
    assert "OpenRouter" in message
    assert notification_owner is False


def test_open_circuit_keeps_the_terminal_code_without_another_notification():
    error = openrouter_key_limit.OpenRouterKeyLimitCircuitOpen("already open")

    code, _message, notification_owner = tasks._classify_generation_exception(error)

    assert code == "OPENROUTER_KEY_LIMIT_EXCEEDED"
    assert notification_owner is False


def test_first_limit_detector_pages_even_in_a_quiet_recovery_sweep():
    assert tasks._generation_failure_notify(
        "OPENROUTER_KEY_LIMIT_EXCEEDED",
        requested=False,
        limit_notification_owner=True,
    ) is True
    assert tasks._generation_failure_notify(
        "OPENROUTER_KEY_LIMIT_EXCEEDED",
        requested=True,
        limit_notification_owner=False,
    ) is False


def test_successful_operator_probe_closes_stored_key_month(monkeypatch):
    closed = []
    monkeypatch.setattr(
        openrouter_key_limit, "close_circuit", lambda **kwargs: closed.append(kwargs)
    )
    attempt = {
        "reason": "OPENROUTER_KEY_LIMIT_EXCEEDED",
        "provider_key_fingerprint": "fingerprint-a",
        "provider_limit_period": "2026-10",
    }

    tasks._close_key_limit_circuit_after_probe(attempt, tasks.GenerationItemState.SUCCEEDED)

    assert closed == [{"fingerprint": "fingerprint-a", "period": "2026-10"}]


def test_image_key_limit_does_not_buy_safety_or_provider_fallbacks(monkeypatch):
    response = httpx.Response(403, request=_REQUEST, text='{"error":"Key limit exceeded"}')
    error = httpx.HTTPStatusError(
        "openrouter images 403: Key limit exceeded",
        request=_REQUEST,
        response=response,
    )
    calls = 0

    def fail_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(image_engine, "_generate_and_upload", fail_once)

    async def run():
        return await image_engine._google_stages(
            asyncio.get_running_loop(),
            ContentType.HEALTH,
            "병원",
            topic="건강 정보",
            direction=None,
            diagnostics={},
            policy_repair=False,
            prior_policy_rejection=None,
            counters=[],
        )

    with pytest.raises(httpx.HTTPStatusError, match="Key limit exceeded"):
        asyncio.run(run())
    assert calls == 1


def test_image_generation_checks_key_limit_circuit_before_budget(monkeypatch):
    def circuit_open():
        raise openrouter_key_limit.OpenRouterKeyLimitCircuitOpen("already open")

    monkeypatch.setattr(openrouter_key_limit, "raise_if_circuit_open", circuit_open)

    with pytest.raises(openrouter_key_limit.OpenRouterKeyLimitCircuitOpen):
        asyncio.run(image_engine.generate_image(ContentType.HEALTH, "hospital"))

"""Pins the signal_store availability policy: both URL env vars are required, no default.

Unset or unreachable must both FAIL. No database needed: the reachable case is never
exercised, and the unreachable probe points at a closed loopback port.
"""

from __future__ import annotations

import socket

import pytest

from tests.operation_run_signal_support import (
    _ASYNC_URL_ENV,
    _SYNC_URL_ENV,
    _require_postgres_reachable,
)


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _expect_probe_failure() -> str:
    """Run the probe and return its failure message; a skip or a pass fails this test.

    Skipped is not a Failed, so pytest.raises(pytest.fail.Exception) would let a skip
    through and report the test SKIPPED: catch it and fail instead.
    """
    try:
        await _require_postgres_reachable()
    except pytest.skip.Exception as exc:
        pytest.fail(f"signal_store probe skipped instead of failing: {exc}")
    except pytest.fail.Exception as exc:
        return str(exc)
    pytest.fail("signal_store probe neither failed nor skipped")


async def test_fails_when_both_url_env_vars_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ASYNC_URL_ENV, raising=False)
    monkeypatch.delenv(_SYNC_URL_ENV, raising=False)

    message = await _expect_probe_failure()

    assert _ASYNC_URL_ENV in message
    assert _SYNC_URL_ENV in message


@pytest.mark.parametrize(
    ("set_env", "missing_env"),
    [(_ASYNC_URL_ENV, _SYNC_URL_ENV), (_SYNC_URL_ENV, _ASYNC_URL_ENV)],
)
async def test_fails_naming_the_other_var_when_only_one_is_set(
    monkeypatch: pytest.MonkeyPatch,
    set_env: str,
    missing_env: str,
) -> None:
    monkeypatch.delenv(missing_env, raising=False)
    monkeypatch.setenv(set_env, "postgresql://explicitly-set")

    message = await _expect_probe_failure()

    assert message.startswith(f"{missing_env} is not set")
    assert set_env not in message


async def test_empty_url_env_var_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ASYNC_URL_ENV, "")
    monkeypatch.setenv(_SYNC_URL_ENV, "postgresql://explicitly-set")

    message = await _expect_probe_failure()

    assert message.startswith(f"{_ASYNC_URL_ENV} is not set")


async def test_fails_when_both_are_set_but_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _closed_loopback_port()
    monkeypatch.setenv(
        _ASYNC_URL_ENV,
        f"postgresql+asyncpg://reputation:reputation@127.0.0.1:{port}/reputation_test",
    )
    monkeypatch.setenv(
        _SYNC_URL_ENV,
        f"postgresql+psycopg2://reputation:reputation@127.0.0.1:{port}/reputation_test",
    )

    message = await _expect_probe_failure()

    assert "unreachable" in message
    assert _ASYNC_URL_ENV in message
    assert _SYNC_URL_ENV in message

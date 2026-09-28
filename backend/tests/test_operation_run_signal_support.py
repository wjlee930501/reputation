"""Pins the signal_store availability policy (same as tests/integration/conftest.py).

No database needed: the probe points at a closed loopback port.
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


async def _probe_closed_port() -> None:
    port = _closed_loopback_port()
    await _require_postgres_reachable(
        f"postgresql+asyncpg://reputation:reputation@127.0.0.1:{port}/reputation_test",
        f"postgresql+psycopg2://reputation:reputation@127.0.0.1:{port}/reputation_test",
    )


@pytest.mark.parametrize("explicit_env", [_ASYNC_URL_ENV, _SYNC_URL_ENV])
async def test_unreachable_db_fails_when_a_url_env_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
    explicit_env: str,
) -> None:
    monkeypatch.delenv(_ASYNC_URL_ENV, raising=False)
    monkeypatch.delenv(_SYNC_URL_ENV, raising=False)
    monkeypatch.setenv(explicit_env, "postgresql://explicitly-set")

    with pytest.raises(pytest.fail.Exception, match=explicit_env):
        await _probe_closed_port()


async def test_unreachable_db_skips_when_no_url_env_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ASYNC_URL_ENV, raising=False)
    monkeypatch.delenv(_SYNC_URL_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        await _probe_closed_port()

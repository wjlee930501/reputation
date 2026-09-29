"""Pins tests/db_env.py: a missing or unreachable test DB URL fails the test, never skips."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from tests.db_env import fail_unreachable, require_db_url, require_db_urls

_FIRST = "DB_ENV_HELPER_TEST_FIRST_URL"
_SECOND = "DB_ENV_HELPER_TEST_SECOND_URL"
# Port 1 on loopback is closed: the connection is refused at once, no DB is involved.
_CLOSED_PORT_URL = "postgresql+psycopg2://x:x@127.0.0.1:1/x"


def _expect_failure(call) -> str:
    """Return the failure message; a skip or a normal return fails this test.

    Skipped is not a Failed, so pytest.raises(pytest.fail.Exception) would let a skip
    through and report the test SKIPPED: catch it and fail instead.
    """
    try:
        call()
    except pytest.skip.Exception as exc:
        pytest.fail(f"helper skipped instead of failing: {exc}")
    except pytest.fail.Exception as exc:
        return str(exc)
    pytest.fail("helper neither failed nor skipped")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FIRST, raising=False)
    monkeypatch.delenv(_SECOND, raising=False)


def test_unset_url_fails_naming_the_variable() -> None:
    message = _expect_failure(lambda: require_db_url(_FIRST))

    assert message.startswith(f"{_FIRST} is not set")


def test_empty_url_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FIRST, "")

    message = _expect_failure(lambda: require_db_url(_FIRST))

    assert message.startswith(f"{_FIRST} is not set")


def test_set_url_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FIRST, "postgresql://example.invalid/db")

    assert require_db_url(_FIRST) == "postgresql://example.invalid/db"


def test_fallback_chain_returns_the_first_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FIRST, "")
    monkeypatch.setenv(_SECOND, "postgresql://second.invalid/db")

    assert require_db_url(_FIRST, _SECOND) == "postgresql://second.invalid/db"


def test_fallback_chain_fails_naming_every_variable_when_all_unset() -> None:
    message = _expect_failure(lambda: require_db_url(_FIRST, _SECOND))

    assert _FIRST in message
    assert _SECOND in message


def test_all_required_fails_naming_only_the_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FIRST, "postgresql://first.invalid/db")
    monkeypatch.setenv(_SECOND, "")

    message = _expect_failure(lambda: require_db_urls(_FIRST, _SECOND))

    assert message.startswith(f"{_SECOND} is not set")
    assert _FIRST not in message


def test_all_required_returns_every_url_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FIRST, "postgresql://first.invalid/db")
    monkeypatch.setenv(_SECOND, "postgresql://second.invalid/db")

    assert require_db_urls(_FIRST, _SECOND) == (
        "postgresql://first.invalid/db",
        "postgresql://second.invalid/db",
    )


def test_unreachable_set_url_fails_naming_the_variable_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FIRST, _CLOSED_PORT_URL)

    def probe() -> None:
        engine = sa.create_engine(require_db_url(_FIRST), connect_args={"connect_timeout": 2})
        try:
            engine.connect().close()
        except OperationalError as exc:
            fail_unreachable(_FIRST, exc)
        finally:
            engine.dispose()

    message = _expect_failure(probe)

    assert message.startswith(f"{_FIRST} is set but its PostgreSQL is unreachable")
    assert "OperationalError" in message
    assert "x:x@" not in message

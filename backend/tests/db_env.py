"""Test database URLs come only from the environment — there is no localhost default.

A DB test whose URL env var is missing fails with the variable's name instead of
silently connecting to (or skipping on) whatever Postgres happens to be on a default
port. Look the URL up at fixture/test time, never at import time, so that collecting a
module with a missing variable does not break the session or the module's non-DB tests.

A set URL whose database refuses the connection fails too (`fail_unreachable`): a skip
there would let CI go green while its Postgres service is down.
"""

from __future__ import annotations

import os
from typing import NoReturn

import pytest


def _missing_message(names: list[str]) -> str:
    verb = "is" if len(names) == 1 else "are"
    return (
        f"{' / '.join(names)} {verb} not set. This test needs an explicit PostgreSQL URL "
        "and has no localhost default; export it (see the backend job env in "
        ".github/workflows/ci.yml for the expected values)."
    )


def require_db_url(*env_names: str) -> str:
    """Return the first non-empty env var of `env_names`, else fail the calling test."""
    if not env_names:
        raise TypeError("require_db_url() needs at least one env var name")
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value
    pytest.fail(_missing_message(list(env_names)), pytrace=False)


def require_db_urls(*env_names: str) -> tuple[str, ...]:
    """Return every env var of `env_names`; fail naming all that are missing or empty."""
    if not env_names:
        raise TypeError("require_db_urls() needs at least one env var name")
    missing = [name for name in env_names if not os.environ.get(name)]
    if missing:
        pytest.fail(_missing_message(missing), pytrace=False)
    return tuple(os.environ[name] for name in env_names)


def fail_unreachable(env_name: str, exc: BaseException) -> NoReturn:
    """Fail the calling test: `env_name` is set but its database could not be connected to.

    Call it from the except block of the connection attempt. The URL is not echoed
    because it may carry a password.
    """
    pytest.fail(
        f"{env_name} is set but its PostgreSQL is unreachable ({type(exc).__name__}). "
        "A set test DB URL must connect; this fails instead of skipping.",
        pytrace=False,
    )

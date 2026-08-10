from __future__ import annotations

import os
from collections.abc import Callable, Coroutine
from typing import TypeVar

from sqlalchemy import text

from app.core import database
from app.workers import lead_diagnosis_tasks, tasks

_Result = TypeVar("_Result")
_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test"
)


async def _select_one() -> int:
    sessions = database.get_async_sessionmaker()
    async with sessions() as db:
        return int((await db.execute(text("SELECT 1"))).scalar_one())


def _reset_loop(owner: object) -> None:
    loop = getattr(owner, "loop", None)
    if loop is not None and not loop.is_closed():
        loop.close()
    if hasattr(owner, "loop"):
        delattr(owner, "loop")


def test_twenty_worker_turns_share_one_async_resource_owner(monkeypatch) -> None:
    """Given mixed Celery tasks, every DB turn must stay on its resource-owning loop."""

    database_url = os.getenv("TASK20_DATABASE_URL", _DEFAULT_DATABASE_URL)
    monkeypatch.setenv("SERVICE", "worker")
    monkeypatch.setattr(database.settings, "DATABASE_URL", database_url)
    database.engine = None
    database.AsyncSessionLocal = None
    _reset_loop(tasks._tls)
    _reset_loop(lead_diagnosis_tasks._tls)

    runners: tuple[Callable[[Coroutine[None, None, int]], int], ...] = (
        tasks._run_async,
        lead_diagnosis_tasks._run_async,
    )
    try:
        observed = [runners[index % 2](_select_one()) for index in range(20)]
        assert observed == [1] * 20
    finally:
        if database.engine is not None:
            owner_loop = getattr(tasks._tls, "loop", None)
            if owner_loop is not None and not owner_loop.is_closed():
                owner_loop.run_until_complete(database.engine.dispose())
        database.engine = None
        database.AsyncSessionLocal = None
        _reset_loop(tasks._tls)
        _reset_loop(lead_diagnosis_tasks._tls)

"""Run a bounded synchronous worker batch on one persistent asyncio loop."""

from __future__ import annotations

import asyncio
import threading
from typing import Coroutine, TypeVar

T = TypeVar("T")
_state = threading.local()


class SyncAsyncBridge:
    """Keep loop-bound SDK/Redis clients valid across every item in one batch."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None

    def __enter__(self) -> "SyncAsyncBridge":
        loop = getattr(_state, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _state.loop = loop
        self._loop = loop
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        # The worker's async clients and Redis pool are loop-bound globals. Keep
        # this thread's loop alive for subsequent batches in the same process.
        self._loop = None

    def run(self, coroutine: Coroutine[object, object, T]) -> T:
        if self._loop is None:
            raise RuntimeError("SyncAsyncBridge must be entered before use")
        return self._loop.run_until_complete(coroutine)


__all__ = ("SyncAsyncBridge",)

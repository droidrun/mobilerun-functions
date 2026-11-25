"""Keep one SDK client's I/O on the event loop that created it."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class LoopOwner:
    """A sync `main` runs in a worker thread and reaches adapters through its own
    `asyncio.run`. The client belongs to the runner loop, so adapter I/O hops back to it."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop

    async def run(self, work: Coroutine[Any, Any, T]) -> T:
        owner = self._loop
        if owner is None or owner is asyncio.get_running_loop():
            return await work
        future = asyncio.run_coroutine_threadsafe(work, owner)
        return await asyncio.wrap_future(future)

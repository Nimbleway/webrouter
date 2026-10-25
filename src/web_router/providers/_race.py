"""The calls of one ``max`` race, handed back in the order they finished.

``concurrent.futures.wait()`` and ``asyncio.wait()`` return sets, which say
nothing about which of several calls finished first; a done callback runs the
moment each finishes, so the queue it feeds is in completion order. The sync
race leaves its losers to finish on their threads -- the provider's ``close()``
waits for them -- while the async race cancels them.
"""

from __future__ import annotations

import asyncio
import queue
from concurrent.futures import Future

from web_router.providers.base import ExtractProvider
from web_router.types import ExtractRequest, ExtractResponse

_Runner = tuple[str, ExtractProvider]
"""One member ready to race: its name beside the instance that was built for it."""


class _Race:
    """The calls of one sync race, handed back in the order they finished.

    ``wait()`` returns a set, which says nothing about which of several calls
    finished first; a done callback runs the moment each finishes, so the
    queue it feeds is in completion order.
    """

    def __init__(self) -> None:
        self._finished: queue.SimpleQueue[tuple[str, Future[ExtractResponse]]] = queue.SimpleQueue()
        self.running = 0

    def add(self, provider_name: str, future: Future[ExtractResponse]) -> None:
        self.running += 1
        future.add_done_callback(lambda done: self._finished.put((provider_name, done)))

    def next(self, timeout: float | None = None) -> tuple[str, Future[ExtractResponse]] | None:
        """The next call to finish, or ``None`` when ``timeout`` ran out first or nothing is running."""
        if self.running == 0:
            return None
        try:
            finished = self._finished.get(timeout=timeout)
        except queue.Empty:
            return None
        self.running -= 1
        return finished


class _AsyncRace:
    """The tasks of one async race, handed back in the order they finished, and cancelled at the end.

    Every task started stays in ``_tasks`` until :meth:`cancel` gathers it,
    finished or not: a loser that failed in the same tick as the winner is
    done by then, and gathering it is what reads its exception. Dropped
    unread, the loop would log "Task exception was never retrieved".
    """

    def __init__(self) -> None:
        self._finished: asyncio.Queue[tuple[str, asyncio.Task[ExtractResponse]]] = asyncio.Queue()
        self._tasks: dict[asyncio.Task[ExtractResponse], str] = {}
        self.running = 0

    def start(self, runners: list[_Runner], delegated: ExtractRequest) -> list[_Runner]:
        """Start every runner's call; returns the empty list of runners left to start."""
        for provider_name, member in runners:
            task = asyncio.create_task(member.aextract(delegated))
            self._tasks[task] = provider_name
            self.running += 1
            task.add_done_callback(self._finished_one)
        return []

    def _finished_one(self, task: asyncio.Task[ExtractResponse]) -> None:
        self._finished.put_nowait((self._tasks[task], task))

    async def next(self, timeout: float | None = None) -> tuple[str, asyncio.Task[ExtractResponse]] | None:
        if self.running == 0:
            return None
        try:
            finished = await asyncio.wait_for(self._finished.get(), timeout)
        except asyncio.TimeoutError:
            return None
        self.running -= 1
        return finished

    async def cancel(self) -> None:
        """Cancel every task still running and read every one's outcome, so no loser's exception goes unretrieved."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

"""``max``: every extract backend at once, and the first document back wins.

The parallel counterpart of extract's ``auto``. ``auto`` tries its members one
after another and stops at the first that works, which costs one fetch when
the first member answers and adds each failed member's latency when it does
not. ``max`` sends the same request to every member at the same time and
returns the first document that comes back, so the caller gets the fastest
answer any backend can give -- and pays for every fetch it started, which is
the trade this provider exists to offer. It is never the default.

A ``hedge`` softens that trade: the first member runs alone for that many
seconds, and the rest are started only if it has not answered by then. Most
requests are then one fetch, and only the slow ones pay for the race.

Only a document wins. A backend that refused the fetch, could not be built, or
answered with nothing is a failure like any other, and when every member fails
the error names each one's reason, as ``auto``'s does. What a slower backend
would have returned is never judged: the first document is the answer.
"""

from __future__ import annotations

import asyncio
import math
import queue
import threading
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, ClassVar

from web_router.constants import MAX_AGGREGATE_FAILURE, MSG_MAX_BAD_HEDGE, PROVIDER_MAX
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.providers.auto import _RECOVERABLE, AutoExtractProvider, _ChainSpec, _exhausted
from web_router.providers.base import ExtractProvider
from web_router.types import ExtractRequest, ExtractResponse

_Runner = tuple[str, ExtractProvider]
"""One member ready to race: its name beside the instance that was built for it."""


class MaxExtractProvider(AutoExtractProvider):
    """Extracts through every member at once and returns the first document back.

    The same members, in the same order, as :class:`AutoExtractProvider`, and
    the same routing on ``format``: a member that cannot return the format
    asked for is left out before anything is fetched. The order matters only
    under a ``hedge``, where it says which member runs alone first.
    """

    name: ClassVar[str] = PROVIDER_MAX

    def __init__(
        self,
        *,
        chain: _ChainSpec | None = None,
        members_config: Mapping[str, Mapping[str, Any]] | None = None,
        hedge: float | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            chain: Backends to race, as on :class:`AutoExtractProvider`; keyed
                by method, this reads the ``"extract"`` entry.
            members_config: The router's ``provider_config``, as on
                :class:`AutoProvider`.
            hedge: Seconds the first member runs alone before the rest are
                started. Unset or zero, every member starts at once.
            **kwargs: Ignored, as on :class:`AutoProvider`.

        Raises:
            ConfigurationError: for a ``hedge`` that is not a finite,
                non-negative number.
        """
        super().__init__(chain=chain, members_config=members_config, **kwargs)
        if hedge is not None and not _is_seconds(hedge):
            raise ConfigurationError(MSG_MAX_BAD_HEDGE.format(hedge=hedge))
        self.hedge: float | None = float(hedge) if hedge else None
        # Every call a sync race has submitted and not yet seen finish, the
        # winner's included until it does. The sync router cannot cancel a
        # thread, so close() waits for all of them rather than shutting a
        # member's client under a live request.
        self._inflight: set[Future[ExtractResponse]] = set()
        self._inflight_lock = threading.Lock()

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        failures: dict[str, WebRouterError] = {}
        first, rest = self._runners(delegated, failures)
        # Not a `with` block: leaving one joins every thread, which would hold
        # the winner until the slowest loser answered.
        pool = ThreadPoolExecutor(max_workers=max(1, len(first) + len(rest)))
        race = _Race()
        try:
            self._start(pool, race, first, delegated)
            while True:
                if race.running == 0 and not rest:
                    raise _exhausted(self.name, failures, MAX_AGGREGATE_FAILURE)
                # With the rest still waiting, the hedge bounds this wait; the
                # last one running is waited for however long it takes.
                finished = race.next(timeout=self.hedge if rest else None) if race.running else None
                if finished is None:
                    # The hedge ran out, or everyone started so far has lost:
                    # the rest get their turn.
                    self._start(pool, race, rest, delegated)
                    rest = []
                    continue
                provider_name, future = finished
                try:
                    return future.result()
                except _RECOVERABLE as exc:
                    failures[provider_name] = exc
                if rest:
                    # The member the hedge covered failed: no point waiting it out.
                    self._start(pool, race, rest, delegated)
                    rest = []
        finally:
            pool.shutdown(wait=False)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        failures: dict[str, WebRouterError] = {}
        first, rest = self._runners(delegated, failures)
        race = _AsyncRace()
        try:
            race.start(first, delegated)
            while True:
                if race.running == 0 and not rest:
                    raise _exhausted(self.name, failures, MAX_AGGREGATE_FAILURE)
                finished = await race.next(timeout=self.hedge if rest else None) if race.running else None
                if finished is None:
                    race.start(rest, delegated)
                    rest = []
                    continue
                provider_name, task = finished
                try:
                    return task.result()
                except _RECOVERABLE as exc:
                    failures[provider_name] = exc
                if rest:
                    race.start(rest, delegated)
                    rest = []
        finally:
            # A loser's call is cancelled rather than left running: on the
            # event loop that costs nothing, and its answer has no reader.
            await race.cancel()

    def _runners(
        self, delegated: ExtractRequest, failures: dict[str, WebRouterError]
    ) -> tuple[list[_Runner], list[_Runner]]:
        """The members to race, built and validated before anything is fetched.

        Built here, on the caller's thread, never on a worker's: two races on
        one instance would otherwise build the same member twice and leak the
        client the second overwrote. A member that cannot be built -- no key,
        no extra -- is its own loss, recorded in ``failures``. Every member
        then validates the request, so a caller's mistake raises here, before
        any fetch, rather than being hidden by whichever member answered
        first. Under a hedge the first member is returned apart from the rest.

        Raises:
            UnsupportedParameterError: from a member that does not map an
                option the request set.
            UnsupportedFormatError: from a member that cannot return the
                format, which :meth:`_eligible` already keeps out of the race.
        """
        runners: list[_Runner] = []
        for position, provider_name in self._eligible(self.format_of(delegated)):
            try:
                member = self._member(position)
            except _RECOVERABLE as exc:
                failures[provider_name] = exc
                continue
            member.validate(delegated)
            runners.append((provider_name, member))
        if self.hedge is None:
            return runners, []
        return runners[:1], runners[1:]

    def _start(
        self,
        pool: ThreadPoolExecutor,
        race: _Race,
        runners: list[_Runner],
        delegated: ExtractRequest,
    ) -> None:
        for provider_name, member in runners:
            future = pool.submit(member.extract, delegated)
            with self._inflight_lock:
                self._inflight.add(future)
            # Registered outside the lock: a call already finished runs its
            # callback here and now, and the callback takes the lock itself.
            future.add_done_callback(self._forget)
            race.add(provider_name, future)

    def _forget(self, future: Future[ExtractResponse]) -> None:
        with self._inflight_lock:
            self._inflight.discard(future)

    def close(self) -> None:
        """Wait for every call a race submitted and has not seen finish, then close the members.

        Not a snapshot: a race on another thread may submit more while this
        waits, so it waits until nothing is in flight at all.
        """
        while True:
            with self._inflight_lock:
                inflight = set(self._inflight)
            if not inflight:
                break
            wait(inflight)
        super().close()


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
    """The tasks of one async race, handed back in the order they finished, and cancelled at the end."""

    def __init__(self) -> None:
        self._finished: asyncio.Queue[tuple[str, asyncio.Task[ExtractResponse]]] = asyncio.Queue()
        self._tasks: dict[asyncio.Task[ExtractResponse], str] = {}
        self.running = 0

    def start(self, runners: list[_Runner], delegated: ExtractRequest) -> None:
        for provider_name, member in runners:
            task = asyncio.create_task(member.aextract(delegated))
            self._tasks[task] = provider_name
            self.running += 1
            task.add_done_callback(self._finished_one)

    def _finished_one(self, task: asyncio.Task[ExtractResponse]) -> None:
        self._finished.put_nowait((self._tasks.pop(task), task))

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
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def _is_seconds(hedge: Any) -> bool:
    """Whether ``hedge`` is a finite, non-negative number -- ``True`` is a number in Python, but not here."""
    return isinstance(hedge, (int, float)) and not isinstance(hedge, bool) and math.isfinite(hedge) and hedge >= 0

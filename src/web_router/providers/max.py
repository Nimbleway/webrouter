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
import threading
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, ClassVar

from web_router.constants import MAX_AGGREGATE_FAILURE, MSG_MAX_BAD_HEDGE, PROVIDER_MAX
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.providers.auto import _RECOVERABLE, AutoExtractProvider, _ChainSpec, _exhausted
from web_router.types import ExtractRequest, ExtractResponse


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
            ConfigurationError: for a ``hedge`` that is not a non-negative number.
        """
        super().__init__(chain=chain, members_config=members_config, **kwargs)
        if hedge is not None and (isinstance(hedge, bool) or not isinstance(hedge, (int, float)) or hedge < 0):
            raise ConfigurationError(MSG_MAX_BAD_HEDGE.format(hedge=hedge))
        self.hedge: float | None = float(hedge) if hedge else None
        # Calls that lost the race and are still running on their threads. The
        # sync router cannot cancel them, so close() waits for them instead of
        # shutting a member's client under a live request.
        self._stragglers: set[Future[ExtractResponse]] = set()
        self._stragglers_lock = threading.Lock()

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        first, rest = self._split(self._eligible(self.format_of(request)))
        pool = ThreadPoolExecutor(max_workers=max(1, len(first) + len(rest)))
        running: dict[Future[ExtractResponse], str] = {}
        failures: dict[str, WebRouterError] = {}

        def start(members: list[tuple[int, str]]) -> None:
            for position, provider_name in members:
                running[pool.submit(self._call, position, delegated)] = provider_name

        # Not a `with` block: leaving one joins every thread, which would hold
        # the winner until the slowest loser answered.
        try:
            start(first)
            while running:
                timeout = self.hedge if rest else None
                done, _ = wait(set(running), timeout=timeout, return_when=FIRST_COMPLETED)
                for future in done:
                    provider_name = running.pop(future)
                    try:
                        return future.result()
                    except _RECOVERABLE as exc:
                        failures[provider_name] = exc
                # The hedge ran out, or the member it covered failed: either
                # way the rest start now.
                start(rest)
                rest = []
            raise _exhausted(self.name, failures, MAX_AGGREGATE_FAILURE)
        finally:
            pool.shutdown(wait=False)
            self._straggle(running)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        first, rest = self._split(self._eligible(self.format_of(request)))
        running: dict[asyncio.Task[ExtractResponse], str] = {}
        failures: dict[str, WebRouterError] = {}

        def start(members: list[tuple[int, str]]) -> None:
            for position, provider_name in members:
                running[asyncio.create_task(self._acall(position, delegated))] = provider_name

        try:
            start(first)
            while running:
                timeout = self.hedge if rest else None
                done, _ = await asyncio.wait(set(running), timeout=timeout, return_when=FIRST_COMPLETED)
                for task in done:
                    provider_name = running.pop(task)
                    try:
                        return task.result()
                    except _RECOVERABLE as exc:
                        failures[provider_name] = exc
                start(rest)
                rest = []
            raise _exhausted(self.name, failures, MAX_AGGREGATE_FAILURE)
        finally:
            # A loser's call is cancelled rather than left running: on the
            # event loop that costs nothing, and its answer has no reader.
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)

    def _call(self, position: int, request: ExtractRequest) -> ExtractResponse:
        """One member's call, built here so a member that cannot be built fails as its own row."""
        return self._member(position).extract(request)

    async def _acall(self, position: int, request: ExtractRequest) -> ExtractResponse:
        return await self._member(position).aextract(request)

    def _split(self, eligible: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        """Who starts now and who waits for the hedge: everyone at once without one."""
        if self.hedge is None:
            return list(eligible), []
        return list(eligible[:1]), list(eligible[1:])

    def _straggle(self, running: Mapping[Future[ExtractResponse], str]) -> None:
        with self._stragglers_lock:
            for future in running:
                if future.done():
                    continue
                self._stragglers.add(future)
                future.add_done_callback(self._forget)

    def _forget(self, future: Future[ExtractResponse]) -> None:
        with self._stragglers_lock:
            self._stragglers.discard(future)

    def close(self) -> None:
        """Wait for every call that lost a race and is still running, then close the members."""
        with self._stragglers_lock:
            stragglers = set(self._stragglers)
        if stragglers:
            wait(stragglers)
        super().close()

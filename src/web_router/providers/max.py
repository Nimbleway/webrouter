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

With several URLs, "first" means the first *complete* answer. A member that
fetched some of the URLs and failed the rest is held, not returned, while
another member can still answer -- most backends take one URL per request, so
the member that fetched less is usually the faster one, and a partial answer
would otherwise be the common case. It is returned when no one else can do
better, as the whole answer the call would have had anyway.
"""

from __future__ import annotations

import contextvars
import math
import threading
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, ClassVar

from web_router.constants import MSG_MAX_BAD_HEDGE, PROVIDER_MAX
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.providers._chain import _RECOVERABLE, _ChainSpec
from web_router.providers._race import _AsyncRace, _Losses, _Race, _Runner
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.base import ExtractProvider
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
            ConfigurationError: for a ``hedge`` that is not a finite,
                non-negative number.
        """
        super().__init__(chain=chain, members_config=members_config, **kwargs)
        if hedge is not None and not _is_seconds(hedge):
            raise ConfigurationError(MSG_MAX_BAD_HEDGE.format(hedge=hedge))
        self.hedge: float | None = float(hedge) if hedge else None
        # What close() must wait for: every sync race that has started and
        # not returned, and every call one submitted that has not finished --
        # the winner's included until it does, and a loser's long after. The
        # sync router cannot cancel a thread, so close() waits for all of it
        # rather than shutting a member's client under a live request. One
        # condition guards both, and the members cache: two callers on their
        # own threads must not build the same member twice.
        self._quiet = threading.Condition()
        self._active = 0
        self._inflight: set[Future[ExtractResponse]] = set()

    def _member(self, position: int) -> ExtractProvider:
        """Build and cache one member, once, however many threads ask at the same time."""
        with self._quiet:
            return super()._member(position)

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        with self._quiet:
            self._active += 1
        try:
            return self._race(request, urls)
        finally:
            with self._quiet:
                self._active -= 1
                self._quiet.notify_all()

    def _race(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        losses = _Losses(self.name)
        first, rest = self._runners(delegated, losses.failures)
        # Not a `with` block: leaving one joins every thread, which would hold
        # the winner until the slowest loser answered.
        pool = ThreadPoolExecutor(max_workers=max(1, len(first) + len(rest)))
        race = _Race()
        try:
            self._start(pool, race, first, delegated)
            while True:
                if race.running == 0 and not rest:
                    return losses.settle()
                # With the rest still waiting, the hedge bounds this wait; the
                # last one running is waited for however long it takes.
                finished = race.next(timeout=self.hedge if rest else None) if race.running else None
                if finished is None:
                    # The hedge ran out, or everyone started so far has lost:
                    # the rest get their turn.
                    rest = self._start(pool, race, rest, delegated)
                    continue
                provider_name, future = finished
                complete = losses.record(provider_name, future.result)
                if complete is not None:
                    return complete
                # The member the hedge covered failed, or answered in part:
                # no point waiting it out.
                rest = self._start(pool, race, rest, delegated)
        finally:
            pool.shutdown(wait=False)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        delegated = self._delegated(request, urls)
        losses = _Losses(self.name)
        first, rest = self._runners(delegated, losses.failures)
        race = _AsyncRace()
        try:
            race.start(first, delegated)
            while True:
                if race.running == 0 and not rest:
                    return losses.settle()
                finished = await race.next(timeout=self.hedge if rest else None) if race.running else None
                if finished is None:
                    rest = race.start(rest, delegated)
                    continue
                provider_name, task = finished
                complete = losses.record(provider_name, task.result)
                if complete is not None:
                    return complete
                rest = race.start(rest, delegated)
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
    ) -> list[_Runner]:
        """Submit every runner's call; returns the empty list of runners left to start."""
        for provider_name, member in runners:
            # In a copy of the caller's context, so a deadline set around the
            # call reaches the member on its worker thread.
            future = pool.submit(contextvars.copy_context().run, member.extract, delegated)
            with self._quiet:
                self._inflight.add(future)
            # Registered outside the lock: a call already finished runs its
            # callback here and now, and the callback takes the lock itself.
            future.add_done_callback(self._forget)
            race.add(provider_name, future)
        return []

    def _forget(self, future: Future[ExtractResponse]) -> None:
        with self._quiet:
            self._inflight.discard(future)
            self._quiet.notify_all()

    def close(self) -> None:
        """Wait until no race is running and no call it submitted is still in flight, then close the members.

        Not a snapshot: a race on another thread may be between starting and
        its first submission, or submit more while this waits, so this waits
        for quiet -- no active race and nothing in flight at once.
        """
        with self._quiet:
            self._quiet.wait_for(lambda: self._active == 0 and not self._inflight)
        super().close()


def _is_seconds(hedge: Any) -> bool:
    """Whether ``hedge`` is a finite, non-negative number -- ``True`` is a number in Python, but not here."""
    return isinstance(hedge, (int, float)) and not isinstance(hedge, bool) and math.isfinite(hedge) and hedge >= 0

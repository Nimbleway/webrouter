"""A router's cooldowns: the backends a 429, a 402 or, when asked, timeouts in a row told ``auto`` to leave alone.

One map per router, keyed by backend name and shared by every method and
thread the router serves, so a burst of calls pays one throttled round trip
per backend rather than one each. ``auto``'s hops read it and set it; a
backend the caller names with ``provider=`` is called whatever its cooldown,
and a throttle it answers starts one, so ``auto`` passes it over afterwards.
Two members of one chain that share a backend's name -- the same backend on
different options -- share its cooldown: they are normally one account.

With ``timeout_cooldown`` on, the default, a backend that timed out on its own
(:mod:`._cooldown_timeouts`) three calls in a row cools down for 30 s. That
cooldown never passes over the last backend a call has left, and an answer ends it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from web_router.constants import DELEGATING_PROVIDERS
from web_router.constants_production import (
    COOLDOWN_DEFAULT_S,
    COOLDOWN_FALLBACK_S,
    COOLDOWN_MAX_S,
    COOLDOWN_STATUS,
    COOLDOWN_STATUSES,
    COOLDOWN_TIMEOUTS,
    HOP_CODE_COOLING_DOWN,
    LOG_COOLDOWN_SET,
    MSG_COOLING_DOWN,
    TIMEOUT_COOLDOWN_S,
)
from web_router.exceptions import ProviderError
from web_router.providers._cooldown_timeouts import Entry, Streaks, backends_own
from web_router.providers._pinning import backend_of
from web_router.types_errors import ERROR_KIND_RATE_LIMIT as _RATE_LIMIT
from web_router.types_errors import ERROR_KIND_TIMEOUT

if TYPE_CHECKING:
    from web_router.providers._runtime import Runtime

_T = TypeVar("_T")

logger = logging.getLogger(__name__)


class CoolingDownError(ProviderError):
    """``auto`` passed a backend over while it cools down: no request was sent, so nothing was billed."""

    hop_code = HOP_CODE_COOLING_DOWN
    """What a decision record's hop says for it, in place of the status that started the cooldown."""


class Cooldowns:
    """Per backend, the monotonic instant it may be tried again, the status that set it, and its timeouts in a row."""

    def __init__(self, *, timeouts: bool = True) -> None:
        self._until: dict[str, Entry] = {}
        self._lock = threading.RLock()  # reentrant: a streak that ends starts its cooldown under the same hold
        self._timeouts = timeouts
        self._streaks = Streaks()

    def note(self, provider: str, exc: BaseException) -> None:
        """Start ``provider``'s cooldown when ``exc`` is a throttle: its ``Retry-After``, else the status's default.

        A timeout of the backend's own making counts towards the cooldown after timeouts in a row; any other
        timeout, the caller's, neither counts nor breaks the streak.
        """
        seconds, status = _cooldown_of(exc)  # first: a throttle among an extract's calls cools it whatever the rest
        if seconds is not None:
            self._start(provider, seconds, status)
        if getattr(exc, "kind", None) != ERROR_KIND_TIMEOUT:
            self._reset(provider)
        elif backends_own(exc):
            self._note_timeout(provider)

    def _note_timeout(self, provider: str) -> None:
        """Count a timeout of ``provider``; the one that completes a streak (:class:`Streaks`) starts its cooldown."""
        if not self._timeouts:
            return
        with self._lock:  # held until the cooldown is set, so an answer arriving meanwhile cannot be overtaken by it
            count = self._streaks.timed_out(backend_of(provider), time.monotonic())
            if count is not None:
                cause = COOLDOWN_TIMEOUTS.format(count=count)
                self._start(provider, TIMEOUT_COOLDOWN_S, None, cause, ERROR_KIND_TIMEOUT)

    def note_answer(self, provider: str, response: object) -> None:
        """Start ``provider``'s cooldown when an answer it gave reports a URL it was throttled on.

        A one-URL-per-call extract folds a 429 on one URL into an answer that read
        the others; the backend is throttled all the same. No ``Retry-After``
        survives into a failure, so the status's default applies.
        """
        self._reset(provider, answered=True)  # it answered: no longer timing out in a row
        failures = getattr(response, "failures", None) or ()
        # Only the backend's own throttle (kind rate_limit): a page the target site answered 429 is kind upstream.
        statuses = [getattr(f, "status_code", None) for f in failures if getattr(f, "kind", None) == _RATE_LIMIT]
        throttled = [status for status in statuses if status in COOLDOWN_STATUSES]
        if throttled:
            status = max(throttled, key=lambda code: COOLDOWN_DEFAULT_S[code])
            self._start(provider, COOLDOWN_DEFAULT_S[status], status)

    def _reset(self, provider: str, answered: bool = False) -> None:
        backend = backend_of(provider)
        with self._lock:
            self._streaks.clear(backend)
            entry = self._until.get(backend)
            if answered and entry is not None and entry.kind == ERROR_KIND_TIMEOUT:
                del self._until[backend]  # a backend the call had left to try answered: it is not hanging

    def _start(
        self, provider: str, seconds: float, status: int | None, cause: str | None = None, kind: str = _RATE_LIMIT
    ) -> None:
        now, provider = time.monotonic(), backend_of(provider)  # a pinned mode cools its whole backend
        with self._lock:
            current = self._until.get(provider)
            if current is None or current.until < now + seconds:
                cause = cause or COOLDOWN_STATUS.format(status=status or _RATE_LIMIT)
                self._until[provider] = Entry(now + seconds, status, cause, kind)
            entry = self._until[provider]  # a longer cooldown already running is kept, and logged as such
        logger.info(LOG_COOLDOWN_SET, provider, entry.until - now, entry.cause)

    def remaining(self, provider: str) -> tuple[float, int | None] | None:
        """Seconds left on ``provider``'s cooldown and the status that set it; ``None`` once it has run out."""
        provider = backend_of(provider)
        with self._lock:
            entry = self._until.get(provider)
            if entry is None:
                return None
            left = entry.until - time.monotonic()
            if left <= 0:
                del self._until[provider]
                return None
            return left, entry.status

    def refusal(self, provider: str, last: bool = False) -> CoolingDownError | None:
        """The error ``auto`` records for passing ``provider`` over while it cools down; ``None`` when it may run.

        ``last``: no other backend is left for the call, so a cooldown after timeouts lets it run.
        """
        with self._lock:  # reentrant: the time left and its cause read from one entry
            left = self.remaining(provider)
            entry = self._until.get(backend_of(provider))
        if left is None or entry is None or (last and entry.kind == ERROR_KIND_TIMEOUT):
            return None
        seconds, status = left
        message = MSG_COOLING_DOWN.format(cause=entry.cause, seconds=seconds)
        error = CoolingDownError(provider, message, kind=entry.kind)
        error.status_code = status
        error.retry_after = seconds
        return error


def _cooldown_of(exc: BaseException) -> tuple[float | None, int | None]:
    """How long ``exc`` asks its backend to be left alone, and the status that says so; ``(None, None)`` if not."""
    if not isinstance(exc, ProviderError):
        return None, None
    if exc.call_errors:  # a sum of several calls: the longest throttle any one of them met
        found = [each for each in map(_cooldown_of, exc.call_errors) if each[0] is not None]
        return max(found, key=lambda each: each[0] or 0.0, default=(None, None))
    status = exc.status_code
    if status not in COOLDOWN_STATUSES and exc.kind != _RATE_LIMIT:
        return None, None
    wait = exc.retry_after
    if wait is None:
        wait = COOLDOWN_DEFAULT_S.get(status, COOLDOWN_FALLBACK_S) if status is not None else COOLDOWN_FALLBACK_S
    return min(max(wait, 0.0), COOLDOWN_MAX_S), status


@contextmanager
def noting(runtime: Runtime | None, backend: object) -> Iterator[Callable[[_T], _T]]:
    """Start a named backend's cooldown when its call, or an answer passed to the yielded hook, is throttled.

    ``auto``'s members are noted by their hops.
    """
    name = getattr(backend, "name", None)
    if runtime is None or not isinstance(name, str) or name in DELEGATING_PROVIDERS:
        yield lambda response: response
        return

    def answered(response: _T) -> _T:
        runtime.cooldowns.note_answer(name, response)
        return response

    try:
        yield answered
    except Exception as exc:
        runtime.cooldowns.note(name, exc)
        raise

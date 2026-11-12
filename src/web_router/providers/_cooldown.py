"""A router's cooldowns: the backends a 429 or a 402 told ``auto`` to leave alone for a while.

One map per router, keyed by backend name and shared by every method and
thread the router serves, so a burst of calls pays one throttled round trip
per backend rather than one each. ``auto``'s hops read it and set it; a
backend the caller names with ``provider=`` is called whatever its cooldown,
and a throttle it answers starts one, so ``auto`` passes it over afterwards.
Two members of one chain that share a backend's name -- the same backend on
different options -- share its cooldown: they are normally one account.
"""

from __future__ import annotations

import logging
import threading
import time

from web_router.constants_production import (
    COOLDOWN_DEFAULT_S,
    COOLDOWN_FALLBACK_S,
    COOLDOWN_MAX_S,
    COOLDOWN_STATUSES,
    LOG_COOLDOWN_SET,
    MSG_COOLING_DOWN,
)
from web_router.exceptions import ProviderError

logger = logging.getLogger(__name__)

_RATE_LIMIT = "rate_limit"


class Cooldowns:
    """Per backend, the monotonic instant it may be tried again and the status that set it."""

    def __init__(self) -> None:
        self._until: dict[str, tuple[float, int | None]] = {}
        self._lock = threading.Lock()

    def note(self, provider: str, exc: BaseException) -> None:
        """Start ``provider``'s cooldown when ``exc`` is a throttle: its ``Retry-After``, else the status's default."""
        seconds, status = _cooldown_of(exc)
        if seconds is not None:
            self._start(provider, seconds, status)

    def note_answer(self, provider: str, response: object) -> None:
        """Start ``provider``'s cooldown when an answer it gave reports a URL it was throttled on.

        A one-URL-per-call extract folds a 429 on one URL into an answer that read
        the others; the backend is throttled all the same. No ``Retry-After``
        survives into a failure, so the status's default applies.
        """
        statuses = [getattr(failure, "status_code", None) for failure in getattr(response, "failures", None) or ()]
        throttled = [status for status in statuses if status in COOLDOWN_STATUSES]
        if throttled:
            status = max(throttled, key=lambda code: COOLDOWN_DEFAULT_S[code])
            self._start(provider, COOLDOWN_DEFAULT_S[status], status)

    def _start(self, provider: str, seconds: float, status: int | None) -> None:
        until = time.monotonic() + seconds
        with self._lock:
            current = self._until.get(provider)
            if current is None or current[0] < until:
                self._until[provider] = (until, status)
        logger.info(LOG_COOLDOWN_SET, provider, seconds, status or _RATE_LIMIT)

    def remaining(self, provider: str) -> tuple[float, int | None] | None:
        """Seconds left on ``provider``'s cooldown and the status that set it; ``None`` once it has run out."""
        with self._lock:
            entry = self._until.get(provider)
            if entry is None:
                return None
            left = entry[0] - time.monotonic()
            if left <= 0:
                del self._until[provider]
                return None
            return left, entry[1]

    def refusal(self, provider: str) -> ProviderError | None:
        """The error ``auto`` records for passing ``provider`` over while it cools down; ``None`` when it may run."""
        left = self.remaining(provider)
        if left is None:
            return None
        seconds, status = left
        error = ProviderError(
            provider, MSG_COOLING_DOWN.format(status=status or _RATE_LIMIT, seconds=seconds), kind=_RATE_LIMIT
        )
        error.status_code = status
        error.retry_after = seconds
        return error


def _cooldown_of(exc: BaseException) -> tuple[float | None, int | None]:
    """How long ``exc`` asks its backend to be left alone, and the status that says so; ``(None, None)`` if not."""
    if not isinstance(exc, ProviderError):
        return None, None
    status = exc.status_code
    if status not in COOLDOWN_STATUSES and exc.kind != _RATE_LIMIT:
        return None, None
    wait = exc.retry_after
    if wait is None:
        wait = COOLDOWN_DEFAULT_S.get(status, COOLDOWN_FALLBACK_S) if status is not None else COOLDOWN_FALLBACK_S
    return min(max(wait, 0.0), COOLDOWN_MAX_S), status

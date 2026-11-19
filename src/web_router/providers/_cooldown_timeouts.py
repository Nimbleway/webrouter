"""Which timeouts count towards a backend's cooldown after timeouts in a row, and the count itself.

Only the backend's own slowness counts: its transport timing out on its own
timeout, or a hop cut off at the router's fixed ``hop_timeout``, which is the
same for every caller. A hop cut off at its share of a caller's time left, or
the call's own deadline running out, says how long that caller would wait, not
how slow the backend is: counting it would let one caller with a short
``timeout=`` hide a healthy backend from every other caller of the router.
"""

from __future__ import annotations

from web_router.constants_production import TIMEOUT_COOLDOWN_AFTER, TIMEOUT_COOLDOWN_S
from web_router.exceptions import ProviderError
from web_router.types_errors import ERROR_KIND_TIMEOUT


class HopTimeoutError(ProviderError):
    """An ``auto`` hop cut off at its cap, with time left in the call for the next backend."""

    def __init__(self, provider: str, message: str, *, original: BaseException, fixed: bool) -> None:
        super().__init__(provider, message, original=original, kind=ERROR_KIND_TIMEOUT)
        self.fixed = fixed
        """Whether the cap was the router's own ``hop_timeout`` rather than a share of the caller's time left."""


def backends_own(exc: BaseException) -> bool:
    """Whether ``exc`` is a timeout of the backend's own making, which counts towards its cooldown."""
    if getattr(exc, "kind", None) != ERROR_KIND_TIMEOUT or not isinstance(exc, ProviderError):
        return False  # the call's deadline (DeadlineExceededError) is the caller's, not the backend's
    if exc.call_errors:  # a sum of an extract's calls: the backend's only when every call's timeout was
        return all(map(backends_own, exc.call_errors))
    return not isinstance(exc, HopTimeoutError) or exc.fixed


class Streaks:
    """Per backend, its timeouts in a row and when the last one was; the caller holds the lock."""

    def __init__(self) -> None:
        self._last: dict[str, tuple[int, float]] = {}

    def timed_out(self, backend: str, now: float) -> int | None:
        """Count a timeout at ``now``; the count when it completes a streak (which starts again), else ``None``.

        A timeout more than :data:`TIMEOUT_COOLDOWN_S` after the one before starts a new streak.
        """
        count, last = self._last.get(backend, (0, now))
        count = 1 if now - last > TIMEOUT_COOLDOWN_S else count + 1
        if count >= TIMEOUT_COOLDOWN_AFTER:
            self._last.pop(backend, None)
            return count
        self._last[backend] = (count, now)
        return None

    def clear(self, backend: str) -> None:
        """Start ``backend``'s count again: it answered, or failed some other way."""
        self._last.pop(backend, None)

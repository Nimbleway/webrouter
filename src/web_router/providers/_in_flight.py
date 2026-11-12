"""What an async ``auto`` call had tried when its deadline cut it off: the member in flight, and those that failed.

An async call is cancelled at its deadline from outside (``asyncio.wait_for``),
so ``auto``'s own loop never reaches the handler that names each member's
reason. Each hop notes itself here instead, and the deadline error raised for
the cut-off call reads it, so ``tried`` names the member in flight as the sync
path does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from web_router.exceptions import DeadlineExceededError


@dataclass
class InFlight:
    """One call's hops so far: the member running now, and each one that failed before it."""

    member: str | None = None
    failures: dict[str, BaseException] = field(default_factory=dict)

    def tried(self, seconds: float) -> str | None:
        """Each member's reason, the one in flight last as cut off at the deadline; ``None`` when nothing ran."""
        reasons = {name: str(exc) for name, exc in self.failures.items()}
        if self.member is not None:
            reasons[self.member] = str(DeadlineExceededError(self.member, seconds))
        return "; ".join(f"{name}: {reason}" for name, reason in reasons.items()) or None


_IN_FLIGHT: ContextVar[InFlight | None] = ContextVar("web_router_in_flight", default=None)


@contextmanager
def tracking() -> Iterator[InFlight]:
    """Note the hops made inside the block on a fresh :class:`InFlight`."""
    flight = InFlight()
    token = _IN_FLIGHT.set(flight)
    try:
        yield flight
    finally:
        _IN_FLIGHT.reset(token)


def in_flight() -> InFlight | None:
    """The hops of the call in flight, or ``None`` outside a tracked call."""
    return _IN_FLIGHT.get()

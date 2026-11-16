"""One vendor call under the deadline in force and the router's concurrency cap: take a slot, call, re-raise late."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, NoReturn, Protocol, TypeVar

from web_router.providers._in_flight import InFlight, tracking
from web_router.providers._runtime import aslot, slot

if TYPE_CHECKING:
    from web_router.providers._deadline import Deadline

_ResultT = TypeVar("_ResultT")


class _Bounded(Protocol):
    @property
    def name(self) -> str: ...

    def _refuse_late(self, deadline: Deadline) -> None: ...

    def _reraise_late(self, deadline: Deadline, exc: Exception, flight: InFlight | None = None) -> NoReturn: ...


def slotted(backend: _Bounded, deadline: Deadline | None, call: Callable[[], _ResultT]) -> _ResultT:
    """``call()`` holding a slot: refused past ``deadline``, and a timeout at it raised as the deadline's."""
    with slot(backend.name, deadline):
        if deadline is None:
            return call()
        backend._refuse_late(deadline)
        try:
            return call()
        except Exception as exc:
            backend._reraise_late(deadline, exc)


async def aslotted(
    backend: _Bounded,
    deadline: Deadline | None,
    start: Callable[[], Awaitable[_ResultT]],
    within: Callable[[Deadline, Awaitable[_ResultT]], Awaitable[_ResultT]],
) -> _ResultT:
    """Async :func:`slotted`: ``start()`` runs once the call may go ahead; ``within`` cuts it off at the deadline."""
    async with aslot(backend.name, deadline):
        if deadline is None:
            return await start()
        backend._refuse_late(deadline)
        with tracking() as flight:
            try:
                return await within(deadline, start())
            except Exception as exc:
                backend._reraise_late(deadline, exc, flight)

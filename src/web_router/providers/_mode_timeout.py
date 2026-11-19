"""Whether a vendor mode's timeout leaves its backend's next mode worth a try.

A timeout in one mode says little about another mode of the same backend:
Exa's ``exa-deep-reasoning`` answers in about eleven seconds at p90,
``exa-instant`` in under one. So when an attempt in a one-call walk (search,
extract, answer) times out (``kind`` ``timeout``, a 408 included), the
backend's next attempt still runs when its measured p90
(:func:`web_router.prices.estimate_latency`) fits the time the call has left
(:func:`~._deadline.current_deadline`). With no deadline, no measured p90, or
too little time, the backend stops for the call, as after any other failure.
A job walk (research, browse) always stops: a run that timed out may already
be billed and running.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from web_router.prices import estimate_latency
from web_router.providers._deadline import current_deadline
from web_router.types_errors import ERROR_KIND_TIMEOUT

if TYPE_CHECKING:
    from web_router.providers._attempt_loop import Order, Walk


def next_mode_fits(walk: Walk, position: int, mode: str | None, exc: BaseException) -> bool:
    """Whether ``exc``, the attempt at ``position`` in ``mode`` timing out, leaves its backend's next attempt to run."""
    if not walk.hops or getattr(exc, "kind", None) != ERROR_KIND_TIMEOUT:
        return False
    following, deadline = next_at(walk.order, position, mode), current_deadline()
    if following is None or deadline is None:
        return False
    measured = estimate_latency(following[0], walk.tool, following[1])
    return measured is not None and measured[1] <= deadline.left()


def next_at(order: Order, position: int, mode: str | None) -> tuple[str, str | None] | None:
    """The (name, mode) of the attempt after ``mode`` at chain ``position`` in ``order``; ``None`` if it is last."""
    later = iter(order)
    for at, _, tried in later:
        if (at, tried) == (position, mode):
            break
    return next(((name, then) for at, name, then in later if at == position), None)

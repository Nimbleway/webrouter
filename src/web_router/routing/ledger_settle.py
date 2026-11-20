"""How a ledger settles what its attempts are charged: a stated cost, or nothing.

Apart from :mod:`.ledger` so each stays small: :class:`~.ledger.Ledger` takes
these methods from :class:`Settling`, and :mod:`.settlement` decides when each
runs. Every one takes the ledger's lock itself.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from web_router.routing.attempts import Attempt
from web_router.routing.pool import PoolChain
from web_router.routing.trail import HopTrail


@dataclass
class _Tried:
    id: str
    provider: str
    est_cost_usd: float | None
    started: float
    attempt: Attempt
    charged: float = 0.0  # what counts toward the caps: the estimate, until :mod:`.settlement` settles it
    own: bool = True  # the call's own attempt, reserved in a shared budget; else a nested call's, only counted


class Settling:
    """The settling half of :class:`~.ledger.Ledger`, which declares this state."""

    pool: PoolChain | None
    spent: float
    reserved: float
    tried: list[_Tried]
    trail: HopTrail
    _lock: threading.Lock

    def refund_open(self, call_id: str) -> Attempt | None:
        """Charge the open attempt, ``call_id``, nothing and list it at $0: it never started. Its attempt, if open."""
        with self._lock:
            opened = self.trail.open
            if opened is None or opened.id != call_id or opened.attempt is None:
                return None
            tried = self.tried[opened.attempt]
            self._charge(tried, 0.0)
            tried.est_cost_usd = 0.0
            return tried.attempt

    def settle_answer(self, provider: str | None, empty: bool, cost: float) -> list[tuple[Attempt, float]]:
        """Charge the attempt that answered (``provider``'s; ``empty``, an empty answer) ``cost``. Returns each attempt
        settled with its new charge."""
        with self._lock:
            answering = self._answered(provider, empty)
            if answering is None:
                return []
            tried = self.tried[answering]
            self._charge(tried, cost)
            return [(tried.attempt, cost)]

    def settle_counted(self, attempt: Attempt, cost: float, *, refunded: bool = False) -> None:
        """Charge ``cost`` for a nested call's ``attempt`` this call counted, if it did; ``refunded`` lists it at $0."""
        with self._lock:
            counted = next((tried for tried in self.tried if tried.attempt is attempt), None)
            if counted is not None:
                self._charge(counted, cost)
                counted.est_cost_usd = 0.0 if refunded else counted.est_cost_usd

    def _charge(self, tried: _Tried, cost: float) -> None:
        """Charge ``tried`` ``cost`` in place of its charge, in the cap and, for its own, the pool. Under the lock.

        A cost past the estimate is reserved without a check: it was billed."""
        delta, tried.charged = cost - tried.charged, cost
        self.spent += delta
        if self.pool is not None and tried.own:
            self.pool.reserve_unchecked(delta) if delta > 0 else self.pool.release(-delta)
            self.reserved += delta

    def _answered(self, provider: str | None, empty: bool) -> int | None:
        """The attempt whose response the call returned, ``provider``'s.

        A response with something in it is the open attempt's: ``auto`` returns it at once. An empty one is the
        first empty answer by that name ``auto`` moved past, which it held while it tried the rest; else the last
        attempt by that name, else the last tried.
        """
        opened = self.trail.open
        named = [i for i, tried in enumerate(self.tried) if tried.provider == provider]
        if not empty and opened is not None and opened.attempt in named:
            return opened.attempt
        held = [i for i in self.trail.answered_empty() if i in named]
        if held:
            return held[0]
        return named[-1] if named else (len(self.tried) - 1 if self.tried else None)

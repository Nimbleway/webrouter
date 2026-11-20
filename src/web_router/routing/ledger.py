"""One budgeted call's ledger: whether each attempt fits what is left, and what the call spent.

:func:`~web_router.routing.budget.budget_scope` keeps one per call and asks :meth:`Ledger.admit` before
every attempt, ``auto``'s members and a named backend alike.

Each attempt is charged its list price by :mod:`.pricing`'s rule when it
starts; :mod:`.settlement` then charges a backend's reported cost instead,
and nothing for an attempt that never started. Under a cost cap a candidate
whose price is unknown -- no row, ``unknown``, or billed by tokens -- never
runs; with no cost cap it runs and the record names it unmeasured. Time is
the call's deadline: a candidate whose measured p90 latency does not fit the
time left is skipped, and one with no measured p90 runs under the deadline,
unless the budget is strict. Inside a shared budget (:mod:`.pool`) an attempt that fits is reserved there too.

A routed call with no budget keeps a ledger too, on a :class:`~web_router.Budget` that caps nothing: it skips
nothing, and records what the call tried and the failures it moved past.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from web_router.constants_budget import (
    OVER_COST,
    OVER_TIME,
    SKIP_LATENCY_UNKNOWN,
    SKIP_NO_TIME,
    SKIP_OVER_COST,
    SKIP_OVER_SHARED,
    SKIP_OVER_TIME,
    SKIP_PRICE_UNKNOWN,
    UNKNOWN_P90,
    UNKNOWN_PRICE,
)
from web_router.exceptions_budget import BudgetExceededError
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget, candidate_id
from web_router.routing.ledger_record import written
from web_router.routing.ledger_settle import Settling, _Tried
from web_router.routing.pool import PoolChain
from web_router.routing.pricing import attempt_cost, attempt_p90, misfit, usd
from web_router.routing.record import error_code
from web_router.routing.trail import HopTrail
from web_router.types_decision import DecisionRecord

_SKIPS = {UNKNOWN_PRICE: SKIP_PRICE_UNKNOWN, UNKNOWN_P90: SKIP_LATENCY_UNKNOWN}


@dataclass
class Ledger(Settling):
    """What one call has tried, skipped and spent under its :class:`~web_router.Budget`.

    Nested calls in other threads or tasks check their attempts against an
    enclosing call's ledger too, so every change to it is made under its lock.
    """

    budget: Budget
    pool: PoolChain | None = None
    """The shared budget the call also spends from, if any."""
    spent: float = 0.0
    """What counts toward the call's own cap: its attempts and those of calls nested in it."""
    reserved: float = 0.0
    """What the call reserved in :attr:`pool` for its own attempts, settled when it ends."""
    tried: list[_Tried] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    sent: bool = False
    """Set once a backend was sent a request, kept when a refused mode is taken back from :attr:`tried`."""
    trail: HopTrail = field(default_factory=HopTrail)
    unmeasured: list[str] = field(default_factory=list)
    stamp: dict[str, Any] = field(default_factory=dict)
    """The ``decision_id`` and ``prompt_sha256`` of a record this ledger starts, with no router's to write into."""
    closed: bool = False
    """Set when the call ends: an attempt made later by work it left running is not this call's to check."""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def admit(self, attempt: Attempt, *, reserve: bool = True) -> bool:
        """Whether ``attempt`` fits what is left; if it does, charge it, and if not, record why.

        ``reserve`` is true for the call's own attempts, which it also reserves
        in the shared budget and keeps in its hop trail; a nested call's attempt
        counts only toward the cap, and leaves the call's own attempt open.
        """
        name = attempt.id
        cost, p90 = attempt_cost(attempt), attempt_p90(attempt)
        with self._lock:
            if self.closed:
                return True
            if (cost is None or p90 is None) and name not in self.unmeasured:
                self.unmeasured.append(name)
            reason = self._misfit(cost, p90) or (self._unreserved(cost) if reserve else None)
            if reason is not None:
                self.skipped[name] = reason
                if reserve:
                    self.trail.skip(name, reason)
                return False
            if reserve:
                self.trail.start(len(self.tried), name)
            self.spent += cost or 0.0
            self.tried.append(_Tried(name, attempt.provider, cost, time.monotonic(), attempt, cost or 0.0, reserve))
            return True

    def undo(self, attempt: Attempt, *, release: bool = False, sent: bool = False) -> None:
        """Take back ``attempt``: a nested call's it refused, or a mode refused once ``sent``; ``release`` frees it."""
        with self._lock:
            self.sent = self.sent or sent
            at = next((i for i in reversed(range(len(self.tried))) if self.tried[i].attempt is attempt), None)
            cost = 0.0 if at is None else self.tried.pop(at).charged
            self.spent -= cost
            if at is not None:
                self.trail.undo(at)
            if release and self.pool is not None and cost:
                self.pool.release(cost)
                self.reserved -= cost

    def close(self) -> float:
        """End the call: later attempts are not its to check. Returns what it reserved, for the pool to settle."""
        with self._lock:
            self.closed = True
            return self.reserved

    def _unreserved(self, cost: float | None) -> str | None:
        """Why the shared budget refused to reserve ``cost``, or ``None`` once reserved (or with no pool)."""
        if self.pool is None or cost is None:
            return None
        left = self.pool.reserve(cost)
        if left is None:
            self.reserved += cost
            return None
        return SKIP_OVER_SHARED.format(cost=usd(cost), left=usd(max(left, 0.0)))

    def _misfit(self, cost: float | None, p90: float | None) -> str | None:
        """Why the attempt does not fit what is left of the money and the time, worded for the record."""
        cap, time_left = self.budget.max_cost_usd, self.budget.time_limit_s
        cost_left = None if cap is None else cap - self.spent
        if time_left is not None and (deadline := current_deadline()) is not None:
            time_left = deadline.left()
        code = misfit(cost, p90, cost_left, None, False)  # cost first, as it is the one that is spent
        if code is None and time_left is not None and time_left <= 0:
            return SKIP_NO_TIME
        code = code or misfit(None, p90, None, time_left, self.budget.strict)
        if code == OVER_COST:
            return SKIP_OVER_COST.format(cost=usd(cost or 0.0), left=usd(max(cost_left or 0.0, 0.0)))
        if code == OVER_TIME:
            return SKIP_OVER_TIME.format(p90=p90, left=time_left)
        return None if code is None else _SKIPS[code]

    def failed(self, tool: str, provider: str, exc: BaseException | str) -> None:
        """Note that ``provider`` failed and the call moved on, by :func:`~.record.error_code` or the code given."""
        with self._lock:
            self.trail.fail(candidate_id(tool, provider), exc if isinstance(exc, str) else error_code(exc))

    def contributed(self, tool: str, provider: str) -> None:
        """Note that ``provider`` read part of the answer though another member's response carries it."""
        with self._lock:
            self.trail.answered(candidate_id(tool, provider))

    def refusal(self) -> BudgetExceededError:
        """The error for a call that started nothing: every candidate skipped, each with its reason."""
        return BudgetExceededError(self.skipped)

    def record(
        self, into: DecisionRecord | None = None, answered: str | None = None, empty: bool = False
    ) -> DecisionRecord:
        """The call's attempts, hops and estimated spend, written into ``into`` (a router's record) or a new one.

        ``answered`` is the provider the response names, ``empty`` whether it is an empty answer; ``ran`` is its
        position in ``into``'s ``merged``."""
        into = into or DecisionRecord()
        into = into if into.decision_id else into.model_copy(update=self.stamp)
        with self._lock:
            attempts, unmeasured = list(self.tried), list(self.unmeasured)
            answering = self._answered(answered, empty)
            hops = self.trail.hops(answering)
        return written(into, attempts, unmeasured, answering, hops)

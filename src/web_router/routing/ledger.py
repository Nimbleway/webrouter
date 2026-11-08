"""One budgeted call's ledger: whether each attempt fits what is left, and what the call spent.

Experimental. :func:`~web_router.routing.budget.budget_scope` keeps one per
call and asks :meth:`Ledger.admit` before every attempt, ``auto``'s members
and a named backend alike.

Money is always an estimate: each attempt is charged its list price from
:mod:`web_router.prices` when it starts, whether it then answers or fails,
since a failed call may still bill. Under a cost cap a candidate whose price
is unknown -- no row, ``unknown``, or billed by tokens -- never runs; with no
cost cap it runs and the record names it unmeasured. Time is the call's
deadline: a candidate whose measured p90 latency does not fit the time left
is skipped, and one with no measured p90 runs under the deadline, which
bounds it, unless the budget is strict. Inside a shared budget
(:mod:`.pool`) an attempt that fits the call is then reserved in the pool,
and skipped when the pool refuses it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from web_router.constants_budget import (
    COST_TOLERANCE_USD,
    SKIP_LATENCY_UNKNOWN,
    SKIP_NO_TIME,
    SKIP_OVER_COST,
    SKIP_OVER_SHARED,
    SKIP_OVER_TIME,
    SKIP_PRICE_UNKNOWN,
)
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.exceptions_budget import BudgetExceededError
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget
from web_router.routing.estimates import attempt_cost, attempt_p90
from web_router.routing.pool import PoolChain
from web_router.types import DecisionAttempt, DecisionHop, DecisionRecord


def _usd(value: float) -> str:
    """Dollars as the price list states them, never rounded to cents: ``$0.025``, ``$0.0011``, ``$1.00``."""
    whole, _, cents = f"{value:.6f}".rstrip("0").partition(".")
    return f"${whole}.{cents.ljust(2, '0')}"


@dataclass
class _Tried:
    id: str
    est_cost_usd: float | None
    started: float
    attempt: Attempt | None = None


@dataclass
class Ledger:
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
    unmeasured: list[str] = field(default_factory=list)
    closed: bool = False
    """Set when the call ends: an attempt made later by work it left running is not this call's to check."""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def admit(self, attempt: Attempt, *, reserve: bool = True) -> bool:
        """Whether ``attempt`` fits what is left; if it does, charge it, and if not, record why.

        ``reserve`` is true for the call's own attempts, which it also reserves
        in the shared budget; a nested call's attempt counts only toward the cap.
        """
        name = f"{attempt.tool}{CANDIDATE_ID_SEPARATOR}{attempt.provider}"
        cost, p90 = attempt_cost(attempt), attempt_p90(attempt)
        with self._lock:
            if self.closed:
                return True
            if (cost is None or p90 is None) and name not in self.unmeasured:
                self.unmeasured.append(name)
            reason = self._over_cost(cost) or self._over_time(p90) or (self._unreserved(cost) if reserve else None)
            if reason is not None:
                self.skipped[name] = reason
                return False
            self.spent += cost or 0.0
            self.tried.append(_Tried(name, cost, time.monotonic(), attempt))
            return True

    def undo(self, attempt: Attempt) -> None:
        """Take back a nested call's ``attempt``, which that call then refused; nothing else is touched."""
        with self._lock:
            tried = next((tried for tried in reversed(self.tried) if tried.attempt is attempt), None)
            if tried is not None:
                self.tried.remove(tried)
                self.spent -= tried.est_cost_usd or 0.0

    def close(self) -> float:
        """End the call: later attempts are not its to check. Returns what it reserved, for the pool to settle."""
        with self._lock:
            self.closed = True
            return self.reserved

    def _over_cost(self, cost: float | None) -> str | None:
        cap = self.budget.max_cost_usd
        if cap is None:
            return None
        if cost is None:
            return SKIP_PRICE_UNKNOWN
        left = cap - self.spent
        if cost > left + COST_TOLERANCE_USD:
            return SKIP_OVER_COST.format(cost=_usd(cost), left=_usd(max(left, 0.0)))
        return None

    def _unreserved(self, cost: float | None) -> str | None:
        """Why the shared budget refused to reserve ``cost``, or ``None`` once reserved (or with no pool)."""
        if self.pool is None or cost is None:
            return None
        left = self.pool.reserve(cost)
        if left is None:
            self.reserved += cost
            return None
        return SKIP_OVER_SHARED.format(cost=_usd(cost), left=_usd(max(left, 0.0)))

    def _over_time(self, p90: float | None) -> str | None:
        if self.budget.time_limit_s is None:
            return None
        deadline = current_deadline()
        left = self.budget.time_limit_s if deadline is None else deadline.left()
        if left <= 0:
            return SKIP_NO_TIME
        if p90 is None:
            return SKIP_LATENCY_UNKNOWN if self.budget.strict else None
        if p90 > left:
            return SKIP_OVER_TIME.format(p90=p90, left=left)
        return None

    def refusal(self) -> BudgetExceededError:
        """The error for a call that started nothing: every candidate skipped, each with its reason."""
        return BudgetExceededError(self.skipped)

    def record(self, into: DecisionRecord | None = None) -> DecisionRecord:
        """The call's estimated spend and attempts, written into ``into`` (a router's record) or a new one."""
        with self._lock:
            attempts, skipped, unmeasured = list(self.tried), dict(self.skipped), list(self.unmeasured)
        ends = [tried.started for tried in attempts[1:]] + [time.monotonic()]
        known = [tried.est_cost_usd for tried in attempts if tried.est_cost_usd is not None]
        update = {
            "estimated_cost_usd": sum(known) if known else None,
            "cost_is_estimate": True,
            "attempts": [
                DecisionAttempt(id=tried.id, est_cost_usd=tried.est_cost_usd, seconds=end - tried.started)
                for tried, end in zip(attempts, ends, strict=True)
            ],
            "hops": [DecisionHop(id=name, error=reason) for name, reason in skipped.items()] or None,
            "unmeasured": unmeasured or None,
        }
        return (into or DecisionRecord()).model_copy(update=update)

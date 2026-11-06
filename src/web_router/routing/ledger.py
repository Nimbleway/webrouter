"""One budgeted call's ledger: whether each attempt fits what is left, and what the call spent.

Experimental. :func:`~web_router.routing.budget.budget_scope` keeps one per
call and asks :meth:`Ledger.admit` before every attempt, ``auto``'s members
and a named backend alike.

Money is always an estimate: each attempt is charged its list price by
:mod:`.pricing`'s rule when it starts, whether it then answers or fails,
since a failed call may still bill. Under a cost cap a candidate whose price
is unknown -- no row, ``unknown``, or billed by tokens -- never runs; with no
cost cap it runs and the record names it unmeasured. Time is the call's
deadline: a candidate whose measured p90 latency does not fit the time left
is skipped, and one with no measured p90 runs under the deadline, which
bounds it, unless the budget is strict. Inside a shared budget
(:mod:`.pool`) an attempt that fits the call is then reserved in the pool,
and skipped when the pool refuses it.

A routed call with no budget keeps a ledger too, on a :class:`~web_router.Budget`
that caps nothing: it skips nothing, and records what the call tried and the
failures it moved past.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from web_router.constants_budget import (
    SKIP_LATENCY_UNKNOWN,
    SKIP_NO_TIME,
    SKIP_OVER_COST,
    SKIP_OVER_SHARED,
    SKIP_OVER_TIME,
    SKIP_PRICE_UNKNOWN,
)
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.exceptions import BudgetExceededError
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget
from web_router.routing.pool import BudgetPool
from web_router.routing.pricing import attempt_cost, attempt_p90, over_cost
from web_router.routing.record import error_code
from web_router.types_decision import DecisionAttempt, DecisionHop, DecisionRecord


def _usd(value: float) -> str:
    return f"${value:.2f}" if value >= 0.01 else f"${value:.4g}"


@dataclass
class _Tried:
    id: str
    est_cost_usd: float | None
    started: float


@dataclass
class Ledger:
    """What one call has tried, skipped and spent under its :class:`~web_router.Budget`."""

    budget: Budget
    pool: BudgetPool | None = None
    """The shared budget the call also spends from, if any; :attr:`spent` is what it reserved there."""
    spent: float = 0.0
    tried: list[_Tried] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    passed: dict[str, str | None] = field(default_factory=dict)
    """Every candidate skipped or failed, in the order it happened, with why."""
    unmeasured: list[str] = field(default_factory=list)
    stamp: dict[str, Any] = field(default_factory=dict)
    """The ``decision_id`` and ``prompt_sha256`` of a record this ledger starts, with no router's to write into."""

    def admit(self, attempt: Attempt) -> bool:
        """Whether ``attempt`` fits what is left; if it does, charge it, and if not, record why."""
        name = f"{attempt.tool}{CANDIDATE_ID_SEPARATOR}{attempt.provider}"
        cost, p90 = attempt_cost(attempt), attempt_p90(attempt)
        if (cost is None or p90 is None) and name not in self.unmeasured:
            self.unmeasured.append(name)
        reason = self._over_cost(cost) or self._over_time(p90) or self._unreserved(cost)
        if reason is not None:
            self.skipped[name] = self.passed[name] = reason
            return False
        self.spent += cost or 0.0
        self.tried.append(_Tried(name, cost, time.monotonic()))
        return True

    def _over_cost(self, cost: float | None) -> str | None:
        cap = self.budget.max_cost_usd
        if cap is None:
            return None
        if cost is None:
            return SKIP_PRICE_UNKNOWN
        left = cap - self.spent
        if over_cost(cost, left):
            return SKIP_OVER_COST.format(cost=_usd(cost), left=_usd(max(left, 0.0)))
        return None

    def _unreserved(self, cost: float | None) -> str | None:
        """Why the shared budget refused to reserve ``cost``, or ``None`` once reserved (or with no pool)."""
        if self.pool is None or cost is None:
            return None
        left = self.pool.reserve(cost)
        if left is None:
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

    def failed(self, tool: str, provider: str, exc: BaseException) -> None:
        """Note that ``provider`` failed and the call moved on, by :func:`~.record.error_code`."""
        self.passed[f"{tool}{CANDIDATE_ID_SEPARATOR}{provider}"] = error_code(exc)

    def _answered(self, provider: str | None) -> str | None:
        """The candidate that answered: the last tried by ``provider``'s name, else the last tried."""
        ids = [tried.id for tried in self.tried]
        named = [i for i in ids if i.partition(CANDIDATE_ID_SEPARATOR)[2] == provider]
        return (named or ids)[-1] if ids else None

    def _hops(self, answered: str | None) -> list[DecisionHop] | None:
        """Every candidate moved past: skipped, failed, or tried and left for another (empty) answer."""
        hops = [DecisionHop(id=name, error=reason) for name, reason in self.passed.items()]
        empty = [t.id for t in self.tried if t.id != answered and t.id not in self.passed]
        return hops + [DecisionHop(id=name) for name in dict.fromkeys(empty)] or None

    def refusal(self) -> BudgetExceededError:
        """The error for a call that started nothing: every candidate skipped, each with its reason."""
        return BudgetExceededError(self.skipped)

    def record(self, into: DecisionRecord | None = None, answered: str | None = None) -> DecisionRecord:
        """The call's attempts, hops and estimated spend, written into ``into`` (a router's record) or a new one.

        ``answered`` is the provider the response names; ``ran`` is its position in ``into``'s ``merged``.
        """
        into = into or DecisionRecord(**self.stamp)
        ended = time.monotonic()
        ends = [tried.started for tried in self.tried[1:]] + [ended]
        known = [tried.est_cost_usd for tried in self.tried if tried.est_cost_usd is not None]
        ran = self._answered(answered)
        update = {
            "estimated_cost_usd": sum(known) if known else None,
            "cost_is_estimate": True,
            "attempts": [
                DecisionAttempt(id=tried.id, est_cost_usd=tried.est_cost_usd, seconds=end - tried.started)
                for tried, end in zip(self.tried, ends, strict=True)
            ],
            "hops": self._hops(ran),
            "ran": into.merged.index(ran) if into.merged and ran in into.merged else into.ran,
            "unmeasured": list(dict.fromkeys([*(into.unmeasured or ()), *self.unmeasured])) or None,
        }
        return into.model_copy(update=update)

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
bounds it, unless the budget is strict.

A routed call with no budget keeps a ledger too, on a :class:`~web_router.Budget`
that caps nothing: it skips nothing, and records what the call tried and the
failures it moved past.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from web_router.constants_budget import (
    OVER_COST,
    OVER_TIME,
    SKIP_LATENCY_UNKNOWN,
    SKIP_NO_TIME,
    SKIP_OVER_COST,
    SKIP_OVER_TIME,
    SKIP_PRICE_UNKNOWN,
    UNKNOWN_P90,
    UNKNOWN_PRICE,
)
from web_router.exceptions_budget import BudgetExceededError
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget, candidate_id
from web_router.routing.pricing import attempt_cost, attempt_p90, misfit
from web_router.routing.record import error_code
from web_router.routing.trail import HopTrail
from web_router.types_decision import DecisionAttempt, DecisionRecord

_SKIPS = {UNKNOWN_PRICE: SKIP_PRICE_UNKNOWN, UNKNOWN_P90: SKIP_LATENCY_UNKNOWN}


def _usd(value: float) -> str:
    """Dollars as the price list states them, never rounded to cents: ``$0.025``, ``$0.0011``, ``$1.00``."""
    whole, _, cents = f"{value:.6f}".rstrip("0").partition(".")
    return f"${whole}.{cents.ljust(2, '0')}"


@dataclass
class _Tried:
    id: str
    provider: str
    est_cost_usd: float | None
    started: float


@dataclass
class Ledger:
    """What one call has tried, skipped and spent under its :class:`~web_router.Budget`."""

    budget: Budget
    spent: float = 0.0
    tried: list[_Tried] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    trail: HopTrail = field(default_factory=HopTrail)
    unmeasured: list[str] = field(default_factory=list)
    stamp: dict[str, Any] = field(default_factory=dict)
    """The ``decision_id`` and ``prompt_sha256`` of a record this ledger starts, with no router's to write into."""

    def admit(self, attempt: Attempt) -> bool:
        """Whether ``attempt`` fits what is left; if it does, charge it, and if not, record why."""
        name = candidate_id(attempt.tool, attempt.provider)
        cost, p90 = attempt_cost(attempt), attempt_p90(attempt)
        if (cost is None or p90 is None) and name not in self.unmeasured:
            self.unmeasured.append(name)
        reason = self._misfit(cost, p90)
        if reason is not None:
            self.skipped[name] = reason
            self.trail.skip(name, reason)
            return False
        self.trail.start(len(self.tried), name)
        self.spent += cost or 0.0
        self.tried.append(_Tried(name, attempt.provider, cost, time.monotonic()))
        return True

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
            return SKIP_OVER_COST.format(cost=_usd(cost or 0.0), left=_usd(max(cost_left or 0.0, 0.0)))
        if code == OVER_TIME:
            return SKIP_OVER_TIME.format(p90=p90, left=time_left)
        return None if code is None else _SKIPS[code]

    def failed(self, tool: str, provider: str, exc: BaseException) -> None:
        """Note that ``provider`` failed and the call moved on, by :func:`~.record.error_code`."""
        self.trail.fail(candidate_id(tool, provider), error_code(exc))

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

    def refusal(self) -> BudgetExceededError:
        """The error for a call that started nothing: every candidate skipped, each with its reason."""
        return BudgetExceededError(self.skipped)

    def record(
        self, into: DecisionRecord | None = None, answered: str | None = None, empty: bool = False
    ) -> DecisionRecord:
        """The call's attempts, hops and estimated spend, written into ``into`` (a router's record) or a new one.

        ``answered`` is the provider the response names, ``empty`` whether it is an empty answer; ``ran`` is its
        position in ``into``'s ``merged``.
        """
        into = into or DecisionRecord()
        into = into if into.decision_id else into.model_copy(update=self.stamp)
        ended = time.monotonic()
        ends = [tried.started for tried in self.tried[1:]] + [ended]
        known = [tried.est_cost_usd for tried in self.tried if tried.est_cost_usd is not None]
        answering = self._answered(answered, empty)
        ran = None if answering is None else self.tried[answering].id
        update = {
            "estimated_cost_usd": sum(known) if known else None,
            "cost_is_estimate": True,
            "attempts": [
                DecisionAttempt(id=tried.id, est_cost_usd=tried.est_cost_usd, seconds=end - tried.started)
                for tried, end in zip(self.tried, ends, strict=True)
            ],
            "hops": self.trail.hops(answering),
            "ran": into.merged.index(ran) if into.merged and ran in into.merged else into.ran,
            "unmeasured": list(dict.fromkeys([*(into.unmeasured or ()), *self.unmeasured])) or None,
        }
        return into.model_copy(update=update)

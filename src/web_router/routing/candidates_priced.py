"""Pricing and prefiltering one method's members, for :mod:`web_router.routing.candidates`.

Experimental. Each member is priced and timed by :mod:`.pricing`'s rule, the one
the budget's ledger charges by, and dropped, with why, when the budget leaves
it out. A member whose price or p90 is unknown, that is keyless, that carries
``provider_options`` or whose place route-eval did not rank is unmeasured.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache

from web_router._chains import chains
from web_router.constants_budget import OVER_COST, OVER_TIME, UNKNOWN_P90, UNKNOWN_PRICE
from web_router.constants_candidates import (
    MSG_DROPPED_OVER_COST,
    MSG_DROPPED_OVER_TIME,
    MSG_DROPPED_UNKNOWN_COST,
    MSG_DROPPED_UNKNOWN_LATENCY,
    RANKED_METHODS,
)
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget, Candidate, ToolName
from web_router.routing.pricing import attempt_cost, attempt_p90, misfit
from web_router.types import NormalizedRequest


@dataclass
class Found:
    """What :func:`build_candidates` has found so far, across the methods."""

    kept: list[Candidate] = field(default_factory=list)
    dropped: dict[str, str] = field(default_factory=dict)
    unmeasured: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    priced: dict[ToolName, NormalizedRequest] = field(default_factory=dict)


def price_members(
    tool: ToolName,
    request: NormalizedRequest,
    eligible: Sequence[tuple[str, bool]],
    budget: Budget | None,
    found: Found,
) -> None:
    """Price and time each of ``tool``'s eligible members on ``request``; keep the ones ``budget`` leaves in."""
    for name, keyless in eligible:
        attempt = Attempt(tool=tool, provider=name, request=request)
        cost, p90 = attempt_cost(attempt), attempt_p90(attempt)
        candidate = Candidate(tool=tool, provider=name, est_cost_usd=cost, est_latency_s=p90)
        if candidate.id in found.seen:
            continue  # a member listed twice is one candidate, at its first place
        found.seen.add(candidate.id)
        if (why := over_budget(cost, p90, budget)) is not None:
            found.dropped[candidate.id] = why
            continue
        found.kept.append(candidate)
        if cost is None or p90 is None or keyless or request.provider_options or name not in _ranked(tool):
            found.unmeasured.append(candidate.id)


_DROPPED = {
    UNKNOWN_PRICE: MSG_DROPPED_UNKNOWN_COST,
    OVER_COST: MSG_DROPPED_OVER_COST,
    UNKNOWN_P90: MSG_DROPPED_UNKNOWN_LATENCY,
    OVER_TIME: MSG_DROPPED_OVER_TIME,
}


def over_budget(cost: float | None, p90: float | None, budget: Budget | None) -> str | None:
    """Why ``budget`` leaves the candidate out, or ``None`` when it fits."""
    if budget is None:
        return None
    cap, limit = budget.max_cost_usd, budget.time_limit_s
    code = misfit(cost, p90, cap, limit, budget.strict)
    return None if code is None else _DROPPED[code].format(cost=cost, cap=cap, p90=p90, limit=limit)


@cache
def _ranked(tool: ToolName) -> frozenset[str]:
    """The members route-eval ranked in ``tool``'s default order; none for a hand-ordered method."""
    if tool not in RANKED_METHODS:
        return frozenset()
    order = chains()[tool]
    return frozenset(order["preferred"]) - frozenset(order.get("unmeasured", ()))

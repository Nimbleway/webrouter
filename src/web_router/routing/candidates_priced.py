"""Pricing and prefiltering one method's members, for :mod:`web_router.routing.candidates`.

Each member is priced and timed by :mod:`.pricing`'s rule, the one
the budget's ledger charges by, and dropped, with why, when the budget leaves
it out. A member whose price or p90 is unknown, that is keyless, that carries
``provider_options`` or whose place route-eval did not rank is unmeasured; so is every vendor mode, which
route-eval ranks none of.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
from web_router.routing.candidate_modes import ModeEntry
from web_router.routing.contract import Budget, Candidate, ToolName, candidate_id
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
    cost_order: dict[ToolName, list[str]] = field(default_factory=dict)

    def unbuilt(self, tool: ToolName, unbuilt: dict[str, str]) -> None:
        """Drop each member of ``tool`` that cannot be built, with why, unless it was built at another place."""
        for name, why in unbuilt.items():
            if (unbuilt_id := candidate_id(tool, name)) not in self.seen:
                self.dropped[unbuilt_id] = why


def price_members(
    tool: ToolName,
    request: NormalizedRequest,
    eligible: Sequence[tuple[str, bool]],
    budget: Budget | None,
    found: Found,
    modes: Callable[[str], Sequence[ModeEntry]] | None = None,
) -> None:
    """Price and time each of ``tool``'s eligible members on ``request``, each followed by the ``modes`` it offers;
    keep the ones ``budget`` leaves in. A mode whose id names a chain entry pinned to it, such as
    ``exa:exa-instant``, is left to that entry: the entry is the candidate a decision can run."""
    members = {candidate_id(tool, name) for name, _ in eligible}
    for name, keyless in eligible:
        if candidate_id(tool, name) in found.seen:
            continue  # a member listed twice is one candidate, at its first place
        for mode, at in [(None, request), *(modes(name) if modes else ())]:
            if mode is None or candidate_id(tool, name, mode) not in members:
                _place(tool, name, mode, at, keyless, budget, found)


def _place(
    tool: ToolName,
    name: str,
    mode: str | None,
    at: NormalizedRequest | str,
    keyless: bool,
    budget: Budget | None,
    found: Found,
) -> None:
    """One candidate, priced on ``at`` -- its request, or why its member refuses the mode -- kept or dropped."""
    cid = candidate_id(tool, name, mode)
    if cid in found.seen:
        return
    found.seen.add(cid)
    if isinstance(at, str):
        found.dropped[cid] = at
        return
    attempt = Attempt(tool=tool, provider=name, request=at)
    cost, p90 = attempt_cost(attempt), attempt_p90(attempt)
    if (why := over_budget(cost, p90, budget)) is not None:
        found.dropped[cid] = why
        return
    found.kept.append(Candidate(tool=tool, provider=name, mode=mode, est_cost_usd=cost, est_latency_s=p90))
    if cost is None or p90 is None or keyless or at.provider_options or mode or name not in _ranked(tool):
        found.unmeasured.append(cid)


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

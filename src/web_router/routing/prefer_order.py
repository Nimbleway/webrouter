"""``prefer``'s order: backends by estimated cost (``cheap``) or measured p90 latency (``fast``), unknowns last.

Experimental. The caller's ``prefer`` orders ``auto``'s members on every call
and plan that runs ``auto``'s default order, whether or not a router is
attached, and a router that ranks a method's backends still decides that
method. ``cheap`` puts a $0 backend first, then the cheapest; ``fast`` the
lowest p90 first. A backend with no estimate goes last, ties keep the static
order -- accuracy first -- and a ``Budget`` still caps: a preference only
orders what the budget lets run. The built-in ``cheapest_first`` router is
this order with ``cheap``, whatever ``prefer`` says.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from web_router.routing.attempts import Attempt, CallRoute
from web_router.routing.candidates_priced import over_budget
from web_router.routing.contract import Budget, Candidate, Prefer, ToolName, candidate_id
from web_router.routing.pricing import attempt_cost, attempt_p90, cost_key
from web_router.routing.record import prompt_of, stamp
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_ESTIMATE: dict[Prefer, Callable[[Attempt], float | None]] = {"cheap": attempt_cost, "fast": attempt_p90}
"""What each preference sorts a member by, as a budget would charge it."""

_CANDIDATE_ESTIMATE: dict[Prefer, Callable[[Candidate], float | None]] = {
    "cheap": lambda candidate: candidate.est_cost_usd,
    "fast": lambda candidate: candidate.est_latency_s,
}
"""The same, read from a :class:`Candidate` a router is shown."""


def preferred(tool: ToolName, names: Sequence[str], request: NormalizedRequest, prefer: Prefer) -> list[str]:
    """``names`` -- ``tool``'s members in static order -- by ``prefer``'s estimate of ``request``, unknowns last."""
    estimate = _ESTIMATE[prefer]
    key = {name: cost_key(estimate(Attempt(tool=tool, provider=name, request=request))) for name in names}
    return sorted(names, key=key.__getitem__)  # stable: ties keep the static order


def preferred_candidates(candidates: Iterable[Candidate], prefer: Prefer) -> list[Candidate]:
    """``candidates`` by ``prefer``'s estimate, unknowns last and ties as offered."""
    estimate = _CANDIDATE_ESTIMATE[prefer]
    return sorted(candidates, key=lambda candidate: cost_key(estimate(candidate)))


def preferred_route(
    tool: ToolName, names: Sequence[str], request: NormalizedRequest, budget: Budget | None, prefer: Prefer
) -> tuple[CallRoute, DecisionRecord]:
    """The preferred walk, recording initially excluded members as a plan does.

    Keep them in the walk so the budget hook can record its skips and raise
    BudgetExceededError when none fit. Setup failures stay in the walk too.
    """
    ordered = preferred(tool, names, request, prefer)
    dropped = {}
    for name in ordered:
        attempt = Attempt(tool=tool, provider=name, request=request)
        if (why := over_budget(attempt_cost(attempt), attempt_p90(attempt), budget)) is not None:
            dropped[candidate_id(tool, name)] = why
    merged = [candidate_id(tool, name) for name in ordered if candidate_id(tool, name) not in dropped]
    return CallRoute(order={tool: ordered}), DecisionRecord(
        merged=merged, dropped=dropped or None, prefer=prefer, **stamp(prompt_of(tool, request)[0])
    )

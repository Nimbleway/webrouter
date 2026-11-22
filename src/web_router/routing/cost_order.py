"""``auto``'s order with no router ranking it: the caller's ``prefer``, else, under a cost cap, cheapest first among
the backends the benchmark cannot tell apart.

With a budget that caps cost, a call on ``auto`` tries its members in this
order rather than the static one, unless a router ranks that method's backends
itself: with no router asked, and on a plan whose router picks the method
alone, as the default ``rules`` does (the candidates carry it for
:mod:`.decide`). The rule is the one
``make chains`` ranks the static order by (``data/chains.json``): best
measured accuracy first, and the members within the method's noise floor of
the most accurate one not yet placed form one tier -- an accuracy gap smaller
than a rerun's spread does not tell them apart. Within a tier this call's own
estimate decides, by the rule the budget charges by: its depth, result count
and URLs priced, a $0 backend first, an unknown price last, ties in the static
order. The static order prices each member at its default mode once, and puts
$0 after every known price; here the request is priced and free is cheapest.

Members the benchmark did not rank (every extract, research and browse
backend, and the keyless and newer search backends) keep their places after
the ranked ones, in the static order: there is no measure of their quality to
trade against cost. Only search and answer are reordered, and only on the
default order: a ``chain`` the caller set, or a backend named, runs as given.
The order is on the response's ``decision.merged``.

A ``prefer`` the caller set replaces this rule on the default order, capped or
not: every eligible member by that estimate (:mod:`.prefer_order`), and the
record names it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from web_router._chains import chains
from web_router.constants import PROVIDER_AUTO
from web_router.constants_budget import ACCURACY_TOLERANCE
from web_router.providers._chain import _configured_in, _default_chain
from web_router.routing.attempts import Attempt, CallRoute
from web_router.routing.contract import Budget, Prefer, ToolName, candidate_id
from web_router.routing.effort_call import Only
from web_router.routing.eligible import eligible_members
from web_router.routing.prefer_order import preferred_route
from web_router.routing.pricing import attempt_cost, cost_key
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord


def cost_ordered(
    tool: ToolName, names: Sequence[str], request: NormalizedRequest, unranked: Collection[str] = ()
) -> list[str]:
    """``names`` -- ``tool``'s eligible members in static order -- in cost order within each accuracy tier.

    A name in ``unranked`` is not ranked though the benchmark measured a backend of that name: it keeps its place.
    """
    data = chains().get(tool) or {}
    measured = data.get("measured") or {}
    accuracy = {name: float(measured[name]["accuracy"]) for name in names if name in measured and name not in unranked}
    if len(accuracy) < 2:
        return list(names)
    floor = float(data["noise_floor"]["value"]) + ACCURACY_TOLERANCE
    static = {name: at for at, name in enumerate(names)}
    left = [name for name in names if name in accuracy]
    ranked: list[str] = []
    while left:
        best = max(accuracy[name] for name in left)
        tier = [name for name in left if best - accuracy[name] <= floor]
        price = {name: cost_key(attempt_cost(Attempt(tool=tool, provider=name, request=request))) for name in tier}
        ranked += sorted(tier, key=lambda name: (*price[name], static[name]))
        left = [name for name in left if name not in tier]
    return ranked + [name for name in names if name not in accuracy]


def cost_route(
    chain: Any,
    tool: ToolName,
    request: NormalizedRequest,
    budget: Budget | None,
    prefer: Prefer | None = None,
    *,
    eligible: Sequence[tuple[str, bool]] | None = None,
) -> tuple[CallRoute | None, DecisionRecord | None]:
    """The order a call no router ranks runs in, by ``prefer`` or under ``budget``, and the record naming it.

    ``None`` for both when the order is the static one: no ``prefer`` and no cost cap, a backend the caller named,
    a ``chain`` the caller set, or, with no ``prefer``, no tier the request's prices reorder.
    ``eligible`` supplies a plan's buildable members; a direct preferred call uses the actual walk.
    """
    capped = budget is not None and budget.max_cost_usd is not None
    if not (capped or prefer) or getattr(chain, "name", None) != PROVIDER_AUTO or not _runs_default(chain):
        return None, None
    members = eligible if eligible is not None else eligible_members(Only(tool, chain), tool, request)[0]
    names = [name for name, _ in members]
    if prefer is not None:
        # Plans offer buildable candidates; direct calls preserve the walk's eligibility and setup failures.
        if eligible is None:
            names = [name for _, name in chain._eligible(request)]
        if not names:
            return None, DecisionRecord(prefer=prefer)
        return preferred_route(tool, names, request, budget, prefer)
    # a keyless member shares its keyed namesake's measure but not its quality: unranked, it keeps its place
    ordered = cost_ordered(tool, names, request, {name for name, keyless in members if keyless})
    if ordered == names:
        return None, None
    return CallRoute(order={tool: ordered}), DecisionRecord(merged=[candidate_id(tool, n) for n in ordered])


def _runs_default(chain: Any) -> bool:
    """Whether ``chain`` -- an ``auto`` -- runs its default order: a ``chain`` the caller set is theirs to keep.

    Names and each member's options both match the default: a chain that differs in either was set on purpose.
    """
    default = getattr(type(chain), "default_chain", None)
    members, resolved = getattr(chain, "_members_config", None), getattr(chain, "_chain", None)
    if default is None or members is None or resolved is None:
        return False
    return list(resolved) == list(_default_chain(default, _configured_in(members)))  # as construction calls it

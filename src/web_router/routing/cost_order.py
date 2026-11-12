"""``auto``'s order under a cost cap: cheapest first among the backends the benchmark cannot tell apart.

With no router asked and a budget that caps cost, a call on ``auto`` tries its
members in this order rather than the static one. The rule is the one
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
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from web_router._chains import chains
from web_router.constants import PROVIDER_AUTO
from web_router.constants_budget import ACCURACY_TOLERANCE
from web_router.providers._chain import _configured_in
from web_router.routing.attempts import Attempt, CallRoute
from web_router.routing.contract import Budget, ToolName, candidate_id
from web_router.routing.effort_call import Only
from web_router.routing.eligible import eligible_members
from web_router.routing.pricing import attempt_cost, cost_key
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord


def orders_by_cost(budget: Budget | None) -> bool:
    """Whether a call under ``budget`` runs in cost order: when it caps cost."""
    return budget is not None and budget.max_cost_usd is not None


def cost_ordered(tool: ToolName, names: Sequence[str], request: NormalizedRequest) -> list[str]:
    """``names`` -- ``tool``'s eligible members in static order -- in cost order within each accuracy tier."""
    data = chains().get(tool) or {}
    measured = data.get("measured") or {}
    accuracy = {name: float(measured[name]["accuracy"]) for name in names if name in measured}
    if len(accuracy) < 2:
        return list(names)
    floor = float(data["noise_floor"]["value"]) + ACCURACY_TOLERANCE
    static = {name: at for at, name in enumerate(names)}
    left = [name for name in names if name in accuracy]
    ranked: list[str] = []
    while left:
        best = max(accuracy[name] for name in left)
        tier = [name for name in left if best - accuracy[name] <= floor]
        ranked += sorted(tier, key=lambda name: (*_price(tool, name, request), static[name]))
        left = [name for name in left if name not in tier]
    return ranked + [name for name in names if name not in accuracy]


def _price(tool: ToolName, provider: str, request: NormalizedRequest) -> tuple[bool, float]:
    """A member's estimate for this request as a sort key: known before unknown, then cheapest."""
    return cost_key(attempt_cost(Attempt(tool=tool, provider=provider, request=request)))


def cost_route(
    chain: Any, tool: ToolName, request: NormalizedRequest, budget: Budget | None
) -> tuple[CallRoute | None, DecisionRecord | None]:
    """The order a call no router orders runs in under ``budget``, and the record naming it; ``None`` for both
    when the order is the static one: no cost cap, a backend the caller named, a ``chain`` the caller set, or
    no tier the request's prices reorder.
    """
    if not orders_by_cost(budget) or getattr(chain, "name", None) != PROVIDER_AUTO or not _default_chain(chain):
        return None, None
    names = [name for name, _ in eligible_members(Only(tool, chain), tool, request)[0]]
    ordered = cost_ordered(tool, names, request)
    if ordered == names:
        return None, None
    record = DecisionRecord(merged=[candidate_id(tool, name) for name in ordered])
    return CallRoute(order={tool: ordered}), record


def _default_chain(chain: Any) -> bool:
    """Whether ``chain`` -- an ``auto`` -- runs its default order: a ``chain`` the caller set is theirs to keep.

    Names and each member's options both match the default: a chain that differs in either was set on purpose.
    """
    default = getattr(type(chain), "default_chain", None)
    members, resolved = getattr(chain, "_members_config", None), getattr(chain, "_chain", None)
    if default is None or members is None or resolved is None:
        return False
    return list(resolved) == list(default(_configured_in(members)))

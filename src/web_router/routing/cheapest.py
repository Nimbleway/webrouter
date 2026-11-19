"""The built-in ``cheapest_first`` router: every call's backends, cheapest estimate first, the free ones leading.

It needs no key and calls nothing. For each call it orders the candidates of
the method the caller called by their estimated cost -- the same estimate a
budget charges by -- so a $0 backend (the keyless search endpoints) is tried
before any paid one, then the cheapest paid ones; a backend
with no known price goes last, in the order it already had, and under a cost
cap never runs at all. Ties keep the static order, which is accuracy first.

Attach it by name, ``WebRouter(router="cheapest_first")``, beside a cost cap
for a batch where every cent counts: unlike ``auto``'s default order under a
budget, which orders by cost only among backends whose measured accuracy is
within the noise floor of each other, it puts price first whatever the
backends score.
"""

from __future__ import annotations

from collections import defaultdict

from web_router.constants_invoke import ROUTER_ROUTE_MODES_ATTR
from web_router.constants_merge import LAST_RESORT_TOOL
from web_router.constants_routing import CHEAPEST_REASON
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest, ToolName
from web_router.routing.invoke import RouterLike
from web_router.routing.pricing import cost_key
from web_router.routing.request_modes import backends


def cheapest_first(req: RouteRequest) -> RouteDecision:
    """Each method's candidates by estimated cost, $0 first and unknown prices last; ties as offered.

    Shown vendor modes for the method the call runs -- the caller's, else search -- it answers ``attempts``
    instead: that method's backends and modes, each at its own price, so the method stays the one it would be.
    """
    by_tool: dict[ToolName, list[Candidate]] = defaultdict(list)
    for candidate in req.candidates:
        by_tool[candidate.tool].append(candidate)
    tool = req.pinned_tool or LAST_RESORT_TOOL
    for candidates in by_tool.values():
        candidates.sort(key=lambda c: cost_key(c.est_cost_usd))  # stable: ties as offered
    if not all(candidate.is_backend for candidate in by_tool[tool]):
        return RouteDecision(attempts=[c.id for c in by_tool[tool]], reason=CHEAPEST_REASON)
    providers = {tool: [c.provider for c in backends(candidates)] for tool, candidates in by_tool.items()}
    return RouteDecision(providers=providers, reason=CHEAPEST_REASON)


setattr(cheapest_first, ROUTER_ROUTE_MODES_ATTR, False)  # backends only unless the caller shows it modes


def cheapest_router() -> RouterLike:
    """``router="cheapest_first"``: the function itself, which takes no ``router_config``."""
    return cheapest_first

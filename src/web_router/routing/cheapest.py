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

from web_router.constants_routing import CHEAPEST_REASON
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest, ToolName
from web_router.routing.invoke import RouterLike
from web_router.routing.pricing import cost_key


def cheapest_first(req: RouteRequest) -> RouteDecision:
    """Each method's candidates by estimated cost, $0 first and unknown prices last; ties as offered."""
    by_tool: dict[ToolName, list[Candidate]] = defaultdict(list)
    for candidate in req.candidates:
        by_tool[candidate.tool].append(candidate)
    providers = {
        tool: [candidate.provider for candidate in sorted(candidates, key=_price_key)]
        for tool, candidates in by_tool.items()
    }
    return RouteDecision(providers=providers, reason=CHEAPEST_REASON)


def _price_key(candidate: Candidate) -> tuple[bool, float]:
    """Known prices first, cheapest first; ``sorted`` is stable, so equal prices keep the offered order."""
    return cost_key(candidate.est_cost_usd)


def cheapest_router() -> RouterLike:
    """``router="cheapest_first"``: the function itself, which takes no ``router_config``."""
    return cheapest_first

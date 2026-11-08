"""Stand-ins for the candidate builder and the merge rule, so an attached router can be called before they land.

Experimental, and temporary: :mod:`.candidates` replaces :func:`chain_candidates`
and :mod:`.merge` replaces :func:`ranked_first`; each keeps the signature here
or changes its one caller in :mod:`.routed`.

* :func:`chain_candidates` offers the members ``auto`` would try for the
  request, with no cost or latency estimate.
* :func:`ranked_first` puts the providers the router ranked for the tool
  first, in its order, then every other candidate in static order. It applies
  nothing else of a decision -- not ``tools``, ``exclude`` or ``params`` --
  and drops a name that is not a candidate.
"""

from __future__ import annotations

from typing import Any

from web_router.constants import PROVIDER_AUTO
from web_router.exceptions import WebRouterError
from web_router.routing.contract import Candidate, RouteDecision, ToolName
from web_router.types import NormalizedRequest


def chain_candidates(tool: ToolName, chain: Any, request: NormalizedRequest) -> tuple[Candidate, ...]:
    """The members ``chain`` would try for ``request``, in its order, when it is ``auto``; otherwise none.

    A request no member can serve offers none, and the call raises as it would
    with no router attached.
    """
    if getattr(chain, "name", None) != PROVIDER_AUTO:
        return ()
    try:
        eligible = chain._eligible(chain.format_of(request) if tool == "extract" else request)
    except WebRouterError:
        return ()
    names = dict.fromkeys(name for _, name in eligible)
    return tuple(Candidate(tool=tool, provider=name) for name in names)


def ranked_first(tool: ToolName, candidates: tuple[Candidate, ...], decision: RouteDecision | None) -> list[str]:
    """The providers to try for ``tool``: the ones ``decision`` ranked, then the rest in static order."""
    static = [candidate.provider for candidate in candidates]
    ranked = list((decision.providers or {}).get(tool, ())) if decision is not None else []
    first = [name for name in dict.fromkeys(ranked) if name in static]
    return first + [name for name in static if name not in first]

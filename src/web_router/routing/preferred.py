"""The built-in ``preferred`` router: every call's backends by a preference, no model asked.

Experimental. ``PreferredRouter(prefer)`` -- ``router="preferred"`` with
``router_config={"prefer": ...}`` -- orders the candidates of each method by
its preference (:mod:`.prefer_order`): ``cheap`` by estimated cost, a $0
backend first; ``fast`` by measured p90 latency; ``accurate`` by the scores it
holds (:mod:`.accuracy`), which it needs. An unknown estimate or score goes
last, ties keep the static order, and under a cost cap a backend with no known
price never runs at all. It needs no key and calls nothing.

At ``cheap``, beside a cost cap, it suits a batch where every cent counts:
unlike ``auto``'s default order under a budget, which orders by cost only among
backends whose measured accuracy is within the noise floor of each other, it
puts price first whatever the backends score.
"""

from __future__ import annotations

from collections import defaultdict
from typing import get_args

from web_router.constants_merge import LAST_RESORT_TOOL
from web_router.constants_prefer import (
    MSG_ACCURATE_NEEDS_SCORES,
    MSG_PREFER_NEEDED,
    MSG_SCORES_UNUSED,
    PREFERRED_REASON,
    PREFERRED_ROUTER_NAME,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.accuracy import AccuracyLike, AccuracyScores, accuracy_scores
from web_router.routing.contract import Candidate, Prefer, RouteDecision, RouteRequest, ToolName
from web_router.routing.prefer_order import preferred_candidates
from web_router.routing.request_modes import backends
from web_router.routing.route_tools import checked_route_modes


class PreferredRouter:
    """Orders each call's backends by ``prefer``: cheap, fast or accurate.

    Does not choose a method: ``plan()`` and ``run()`` default to search even for a URL prompt,
    unless the caller names a method (for example, ``tool="extract"``).
    """

    def __init__(self, prefer: Prefer, accuracy: AccuracyLike | None = None, route_modes: bool | None = None) -> None:
        """
        Args:
            prefer: ``"cheap"``, ``"fast"`` or ``"accurate"``.
            accuracy: The scores ``accurate`` ranks by, or the path of a JSON file of them (:mod:`.accuracy`).
            route_modes: Whether it is shown vendor modes; by default, when ``accuracy`` scores one.

        Raises:
            ConfigurationError: for another ``prefer``, ``accurate`` with no scores, scores with another
                ``prefer``, which never reads them, scores
                :func:`~.accuracy.accuracy_scores` refuses, or a ``route_modes`` that is not a bool.
        """
        if prefer not in get_args(Prefer):
            raise ConfigurationError(MSG_PREFER_NEEDED.format(got=prefer))
        if accuracy is not None and prefer != "accurate":
            raise ConfigurationError(MSG_SCORES_UNUSED.format(prefer=prefer))
        self.prefer: Prefer = prefer
        self.router_name = PREFERRED_ROUTER_NAME
        self.scores = accuracy_scores(accuracy)
        if prefer == "accurate" and not self.scores:
            raise ConfigurationError(MSG_ACCURATE_NEEDS_SCORES)
        moded = any(":" in key for keys in (self.scores or {}).values() for key in keys)
        self.route_modes = moded if route_modes is None else checked_route_modes(route_modes, self)

    def __repr__(self) -> str:
        return f"PreferredRouter({self.prefer!r})"

    def route(self, req: RouteRequest) -> RouteDecision:
        """Each method's candidates by the preference: see :func:`ordered`."""
        return ordered(req, self.prefer, self.scores)


def ordered(req: RouteRequest, prefer: Prefer, scores: AccuracyScores | None) -> RouteDecision:
    """Each method's candidates by ``prefer``, unknowns last; ties as offered.

    Shown vendor modes for the method the call runs -- the caller's, else search -- it answers ``attempts``
    instead: that method's backends and modes, each at its own estimate, so the method stays the one it would be.
    """
    reason = PREFERRED_REASON.format(prefer=prefer)
    by_tool: dict[ToolName, list[Candidate]] = defaultdict(list)
    for candidate in req.candidates:
        by_tool[candidate.tool].append(candidate)
    tool = req.pinned_tool or LAST_RESORT_TOOL
    for tool_of, candidates in by_tool.items():
        by_tool[tool_of] = preferred_candidates(candidates, prefer, scores)
    if not all(candidate.is_backend for candidate in by_tool[tool]):
        return RouteDecision(attempts=[c.id for c in by_tool[tool]], reason=reason, prefer=prefer)
    providers = {tool: [c.provider for c in backends(candidates)] for tool, candidates in by_tool.items()}
    return RouteDecision(providers=providers, reason=reason, prefer=prefer)

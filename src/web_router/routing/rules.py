"""Deprecated rules router, retained for one release with its original method selection.

URLs select extract; an explicit research request selects research then search;
other prompts select search. The default router is now ``data-driven``.
``examples/router_rules.py`` is a starting point for a custom rules router.
"""

from __future__ import annotations

import re
import warnings

from web_router.constants_routing import (
    MSG_RULES_DEPRECATED,
    RULES_DEFINITION_PATTERN,
    RULES_REASON_NO_URL,
    RULES_REASON_RESEARCH,
    RULES_REASON_URL,
    RULES_RESEARCH_PATTERN,
    RULES_ROUTER_NAME,
)
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName

_RESEARCH = re.compile(RULES_RESEARCH_PATTERN, re.IGNORECASE)
_DEFINITION = re.compile(RULES_DEFINITION_PATTERN, re.IGNORECASE)


def rules(req: RouteRequest) -> RouteDecision:
    """``extract`` for a prompt with a URL, ``research`` for a clear ask for it, else ``search``.

    A pinned method is left as pinned, and ``research`` with no backend to run it on searches instead.
    """
    if req.urls:
        return RouteDecision(tools=["extract"], reason=RULES_REASON_URL)
    if _RESEARCH.search(req.prompt) and not _DEFINITION.match(req.prompt):
        return RouteDecision(tools=["research", "search"], reason=RULES_REASON_RESEARCH)
    return RouteDecision(tools=["search"], reason=RULES_REASON_NO_URL)


class RulesRouter:
    """:func:`rules` as ``router="rules"`` builds it: asked by ``plan()`` and ``run()`` alone, named ``rules``."""

    route_tools: tuple[ToolName, ...] = ()
    route_modes = False
    router_name = RULES_ROUTER_NAME

    def __init__(self) -> None:
        warnings.warn(MSG_RULES_DEPRECATED, FutureWarning, stacklevel=2)

    def route(self, req: RouteRequest) -> RouteDecision:
        return rules(req)

"""The built-in rules router, the routers' default: a URL is read, a clear ask for research researched, else search.

Experimental. It needs no key and calls nothing. It reads the URLs
:meth:`~web_router.WebRouter.plan` found in the prompt (after ``url_policy``)
and the prompt's own words, and picks ``extract`` when there is a URL;
``research`` when the prompt asks in so many words for a report, a comparison
or an in-depth analysis (:data:`~web_router.constants_routing.RULES_RESEARCH_PATTERN`),
with ``search`` after it for when no research backend is configured; and
``search`` otherwise. It never picks ``browse`` or ``answer``: a caller who wants
them calls them. The backends' order stays the static one.

``WebRouter()`` and ``AsyncWebRouter()`` attach it by name, ``router="rules"``,
as :class:`RulesRouter`, which declares ``route_tools=()``: only
:meth:`~web_router.WebRouter.plan` and :meth:`~web_router.WebRouter.run` ask
it, and ``search()``, ``extract()`` and the other methods run as they would with
no router. ``router=None`` attaches none. The MCP server attaches it by default
too; ``WEB_ROUTER_MCP_ROUTER`` replaces it.
"""

from __future__ import annotations

import re

from web_router.constants_routing import (
    RULES_REASON_NO_URL,
    RULES_REASON_RESEARCH,
    RULES_REASON_URL,
    RULES_RESEARCH_PATTERN,
    RULES_ROUTER_NAME,
)
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName

_RESEARCH = re.compile(RULES_RESEARCH_PATTERN, re.IGNORECASE)


def rules(req: RouteRequest) -> RouteDecision:
    """``extract`` for a prompt with a URL, ``research`` for a clear ask for it, else ``search``.

    A pinned method is left as pinned, and ``research`` with no backend to run it on searches instead.
    """
    if req.urls:
        return RouteDecision(tools=["extract"], reason=RULES_REASON_URL)
    if _RESEARCH.search(req.prompt):
        return RouteDecision(tools=["research", "search"], reason=RULES_REASON_RESEARCH)
    return RouteDecision(tools=["search"], reason=RULES_REASON_NO_URL)


class RulesRouter:
    """:func:`rules` as ``router="rules"`` builds it: asked by ``plan()`` and ``run()`` alone, named ``rules``."""

    route_tools: tuple[ToolName, ...] = ()
    router_name = RULES_ROUTER_NAME

    def route(self, req: RouteRequest) -> RouteDecision:
        return rules(req)

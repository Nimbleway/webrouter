"""The built-in rules router: a prompt that names an ``http(s)`` URL is read, anything else is searched.

Experimental. It needs no key and calls nothing: it reads the URLs
:meth:`~web_router.WebRouter.plan` found in the prompt (after ``url_policy``),
picks ``extract`` when there is one and ``search`` otherwise, and leaves the
backends' order to the static one. The MCP server attaches it by default; any
router can replace it, through ``router=`` or the server's
``WEB_ROUTER_MCP_ROUTER``.
"""

from __future__ import annotations

from web_router.constants_routing import RULES_REASON_NO_URL, RULES_REASON_URL
from web_router.routing.contract import RouteDecision, RouteRequest


def rules(req: RouteRequest) -> RouteDecision:
    """``extract`` for a prompt with a URL, else ``search``; a pinned method is left as pinned."""
    if req.urls:
        return RouteDecision(tools=["extract"], reason=RULES_REASON_URL)
    return RouteDecision(tools=["search"], reason=RULES_REASON_NO_URL)

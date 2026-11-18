"""The tools that are handlers over the whole session rather than methods of :class:`WebRouterTools`.

The routing tools and the vendor tools, each taking the session's tools and
its arguments; :class:`~web_router.mcp.tools.WebRouterTools` registers them
after its own, in this order.
"""

from __future__ import annotations

from web_router.mcp.routing import ROUTING_TOOLS
from web_router.mcp.vendor_tools import TOOLS as VENDOR_TOOLS

SESSION_TOOLS = {**ROUTING_TOOLS, **VENDOR_TOOLS}
"""Every session-level tool by name: ``web_run`` and the budget tools, then ``vendors``, ``vendor_endpoint`` and
``call_vendor``."""

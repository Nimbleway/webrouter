"""What a call cost and how long it took, as a shaped result carries it.

Kept apart from :mod:`web_router.mcp.shaping`, so that file stops growing.
Every routable response carries ``elapsed_s``, ``cost_usd`` and ``cost_source``;
a shaped result keeps them, rounded, so a client sees what each call spent
without fetching the raw payload. They are a few bytes, so no cut touches them,
and an unset one is left out as every empty key is.
"""

from __future__ import annotations

from typing import Any

from web_router.constants_mcp_context import MCP_COST_DIGITS, MCP_ELAPSED_DIGITS
from web_router.types_cost import CostSource


def cost_fields(response: Any) -> dict[str, Any]:
    """``response``'s ``elapsed_s``, ``cost_usd`` and ``cost_source``, rounded, each left out when unset."""
    elapsed: float | None = getattr(response, "elapsed_s", None)
    cost: float | None = getattr(response, "cost_usd", None)
    source: CostSource | None = getattr(response, "cost_source", None)
    fields = {
        "elapsed_s": None if elapsed is None else round(elapsed, MCP_ELAPSED_DIGITS),
        "cost_usd": None if cost is None else round(cost, MCP_COST_DIGITS),
        "cost_source": source,
    }
    return {key: value for key, value in fields.items() if value is not None}

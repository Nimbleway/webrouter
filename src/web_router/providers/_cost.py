"""A backend's own statement of what a call cost: read from its payload, never estimated here.

A provider whose response states its cost -- Perplexity's ``usage.cost``, Exa's
``costDollars`` -- passes the figure to its response as ``cost_usd`` with
``cost_source="reported"``. A figure that is missing, not a number, or in
another currency than US dollars is no statement, and the router estimates one
instead (:mod:`web_router.routing.metered`).
"""

from __future__ import annotations

import math
from typing import Any

from web_router.constants_cost import COST_CURRENCY_FIELD, COST_CURRENCY_USD, COST_REPORTED


def stated_cost(payload: Any, *path: str) -> dict[str, Any]:
    """``cost_usd`` and ``cost_source`` for a response, from the number at ``path`` in ``payload``; ``{}`` without one.

    The object holding the number may name its currency; one that is not US dollars states nothing usable.
    """
    holder: Any = None
    value = payload
    for key in path:
        holder = value
        value = value.get(key) if isinstance(value, dict) else None
    currency = holder.get(COST_CURRENCY_FIELD) if isinstance(holder, dict) else None
    if currency is not None and str(currency).upper() != COST_CURRENCY_USD:
        return {}
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return {}
    return {"cost_usd": float(value), "cost_source": COST_REPORTED}

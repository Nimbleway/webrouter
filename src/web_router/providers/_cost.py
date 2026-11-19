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

from web_router.constants_cost import COST_CURRENCY_FIELD, COST_CURRENCY_USD, COST_KEYLESS, COST_REPORTED


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
    if isinstance(value, bool) or not isinstance(value, int | float):
        return {}
    try:
        cost = float(value)  # an int too large for a float states nothing usable
    except OverflowError:
        return {}
    if not math.isfinite(cost) or cost < 0:
        return {}
    return {"cost_usd": cost, "cost_source": COST_REPORTED}


def keyless_cost(provider: object) -> dict[str, Any]:
    """``cost_usd=0.0`` and ``cost_source="keyless"`` when ``provider`` calls with no credential; ``{}`` with one.

    For a backend's keyless tier (``allow_keyless``), which bills nothing. A budget charges the call its list
    price before it starts, since it cannot tell the tier then, and settles it to $0 when it answers.
    """
    if getattr(provider, "holds_credential", True) or _sends_auth_header(provider):
        return {}
    return {"cost_usd": 0.0, "cost_source": COST_KEYLESS}


def _sends_auth_header(provider: object) -> bool:
    """Whether an HTTP provider's client authenticates anyway: the caller's ``headers=`` carry its credential
    header, or its ``auth=`` is set."""
    client = getattr(provider, "_client_kwargs", None) or {}
    header, headers = getattr(provider, "api_key_header", None), client.get("headers") or {}
    named = isinstance(header, str) and header.lower() in {str(name).lower() for name in headers}
    return named or client.get("auth") is not None

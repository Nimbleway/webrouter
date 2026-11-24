"""Deprecated spelling of the ``preferred`` router at ``cheap``, retained for one release."""

from __future__ import annotations

import warnings

from web_router.constants_invoke import ROUTER_ROUTE_MODES_ATTR
from web_router.constants_routing import CHEAPEST_ROUTER_NAME, MSG_CHEAPEST_DEPRECATED
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.preferred import PreferredRouter, ordered


def cheapest_first(req: RouteRequest) -> RouteDecision:
    """Order by cost as ``PreferredRouter('cheap')`` does, warning about the deprecated spelling."""
    warnings.warn(MSG_CHEAPEST_DEPRECATED, DeprecationWarning, stacklevel=2)
    return ordered(req, "cheap", None)


setattr(cheapest_first, ROUTER_ROUTE_MODES_ATTR, False)  # preserve the alias's backend-only default


def cheapest_router() -> PreferredRouter:
    """Build the deprecated named router, warning once when it is built rather than on every call."""
    warnings.warn(MSG_CHEAPEST_DEPRECATED, DeprecationWarning, stacklevel=2)
    router = PreferredRouter("cheap")
    router.router_name = CHEAPEST_ROUTER_NAME
    return router

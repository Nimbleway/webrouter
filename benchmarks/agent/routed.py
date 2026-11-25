"""The agent benchmark's routed arms: web-router's tools on ``auto``, on a router of their own that a search asks.

``web-router:data-driven`` runs the same tools as ``web-router:auto``. The difference is its router: the default
data-driven router, asked on every search (``route_tools=("search",)``), first tries the search backends its
provider table measured, each in the vendor tier it was measured in (``search:keenable:keenable-pro+fast``), then
the rest in ``auto``'s order. ``web-router:auto`` never asks it, so against that arm the router's choice of
backends and tiers is the one difference. A backend it picks is not tried again at its default tier, and with
fewer than two measured backends to choose from it picks none, leaving ``auto``'s order.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from web_router.routing.data_driven import DataDrivenRouter

ROUTED_ARMS: dict[str, dict[str, Any]] = {
    "data-driven": {"router": "data-driven", "route_tools": ("search",)},
}
"""``web-router:<name>``: the options of the router each routed arm runs on."""


def routed_routers(
    make: Callable[..., Any], dataset: Any, asked: Collection[str | None], plain: dict[str, Any] | None
) -> dict[str, Any]:
    """A router of its own for each routed arm ``asked``: ``make(dataset, ...)`` on ``plain``, the other arms' config.

    ``run()`` passes its withholding router, so a routed arm keeps the answer-key pages from the agent too.
    """
    routers = ((name, options) for name, options in ROUTED_ARMS.items() if name in asked)
    return {name: make(dataset, provider_config=plain, **options) for name, options in routers}


def routed_record(name: str | None) -> dict[str, Any]:
    """What ``run.json`` adds for a routed arm: its router and the data release it decided with; else nothing."""
    if name not in ROUTED_ARMS:
        return {}
    options = ROUTED_ARMS[name]
    return {"router": {**options, "route_tools": list(options["route_tools"]), "release": DataDrivenRouter().version}}

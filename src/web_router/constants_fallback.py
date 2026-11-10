"""The built-in ``fallback`` router: its name, how it names its members, and its messages.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants_invoke`, so that file stops growing.
"""

from __future__ import annotations

from typing import Final

FALLBACK_ROUTER_NAME: Final = "fallback"
"""The name ``router=`` takes for the built-in router that asks a list of routers in turn."""

FALLBACK_DISPLAY_NAME: Final = "fallback({members})"
"""What a decision record calls it: its members' names, in the order it asks them."""

FALLBACK_CUSTOM_NAME: Final = "custom"
"""What it calls a member passed in as a function or object that sets no ``router_name``: never its import path."""

FALLBACK_NAME_KEY: Final = "name"
"""The key of a member given as a mapping that holds the registered name; its other keys are that router's config."""

FALLBACK_MEMBERS_KEY: Final = "routers"
"""The ``router_config`` key holding its members."""

FALLBACK_MARGIN_S: Final = 0.02
"""Seconds the fallback router keeps back from its deadline: 20 ms.

It stops asking members that much before the call stops waiting on it, so its answer -- the members it asked
included -- always arrives in time to be read. Its own default limit adds the margin to its members' sum, so
with time to spare each member still gets its whole ``timeout_s``.
"""

ROUTER_OK: Final = "ok"
"""A member's outcome in the decision record when it answered with a decision that decides something."""

MSG_FALLBACK_ROUTERS = "router='fallback' takes router_config={{'routers': [...]}}, a non-empty list; got {got}."
MSG_FALLBACK_MEMBER = (
    "router='fallback': member {index} must be a registered router name, a mapping with a 'name', or a router "
    "function or object; got {kind}."
)
MSG_FALLBACK_TIMEOUT = (
    "router='fallback': timeout_s must be more than its {margin} s margin, which it keeps back from every "
    "member; got {got!r}."
)

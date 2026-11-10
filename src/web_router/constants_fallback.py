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

ROUTER_OK: Final = "ok"
"""A member's outcome in the decision record when it answered with a decision that decides something."""

MSG_FALLBACK_ROUTERS = "router='fallback' takes router_config={{'routers': [...]}}, a non-empty list; got {got}."
MSG_FALLBACK_MEMBER = (
    "router='fallback': member {index} must be a registered router name, a mapping with a 'name', or a router "
    "function or object; got {kind}."
)

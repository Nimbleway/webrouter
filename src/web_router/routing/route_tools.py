"""Which tools ask the attached router, whether it is shown vendor modes -- the caller's choice, else the router's --
which modes it asks for beyond those, and what a router prefers."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import cast, get_args

from web_router.constants_invoke import (
    MSG_PREFER,
    MSG_ROUTE_MODES,
    MSG_ROUTE_TOOLS,
    MSG_ROUTER_EXTRA_MODES,
    MSG_ROUTER_ROUTE_MODES,
    MSG_ROUTER_ROUTE_TOOLS,
    ROUTER_EXTRA_MODES_ATTR,
    ROUTER_ROUTE_MODES_ATTR,
    ROUTER_ROUTE_TOOLS_ATTR,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Prefer, ToolName

ALL_TOOLS: frozenset[ToolName] = frozenset(get_args(ToolName))
"""Every tool a router can be consulted on."""


def checked_route_tools(route_tools: Collection[ToolName] | None, router: object = None) -> frozenset[ToolName]:
    """The tools that ask the router: the caller's ``route_tools``, else the router's own, else all five.

    Raises:
        ConfigurationError: for a ``route_tools`` -- the caller's, or the one ``router`` declares -- that is a
            string, not a collection, or names something other than the five tools.
    """
    message = MSG_ROUTE_TOOLS
    if route_tools is None:
        route_tools = getattr(router, ROUTER_ROUTE_TOOLS_ATTR, None)
        message = MSG_ROUTER_ROUTE_TOOLS
    if route_tools is None:
        return ALL_TOOLS
    # Iterated once: a collection that reads differently each time is held to this one reading.
    tools = None if isinstance(route_tools, str) or not isinstance(route_tools, Collection) else tuple(route_tools)
    if (
        tools is None
        or not all(isinstance(tool, str) for tool in tools)  # an unhashable member never reaches set()
        or not set(tools) <= ALL_TOOLS
    ):
        known = ", ".join(get_args(ToolName))
        raise ConfigurationError(message.format(known=known, got=route_tools))
    return frozenset(tools)


def checked_route_modes(route_modes: object, router: object = None) -> bool:
    """Whether the router is shown a candidate per vendor mode: the caller's ``route_modes``, else the router's own,
    else no.

    Raises:
        ConfigurationError: for a ``route_modes`` -- the caller's, or the one ``router`` declares -- that is not a bool.
    """
    message = MSG_ROUTE_MODES
    if route_modes is None:
        route_modes = getattr(router, ROUTER_ROUTE_MODES_ATTR, None)
        message = MSG_ROUTER_ROUTE_MODES
    if route_modes is None:
        return False
    if not isinstance(route_modes, bool):
        raise ConfigurationError(message.format(got=route_modes))
    return route_modes


def checked_extra_modes(router: object) -> dict[str, tuple[str, ...]]:
    """The vendor modes ``router`` asks to be shown beyond a backend's offers, per search backend; none by default.

    Raises:
        ConfigurationError: for an ``extra_modes`` that is not a mapping of backend name to a list of mode ids.
    """
    declared = getattr(router, ROUTER_EXTRA_MODES_ATTR, None)
    if declared is None:
        return {}
    if not isinstance(declared, Mapping) or not all(_names(name, ids) for name, ids in declared.items()):
        raise ConfigurationError(MSG_ROUTER_EXTRA_MODES.format(got=declared))
    return {name: tuple(ids) for name, ids in declared.items()}


def _names(name: object, ids: object) -> bool:
    """Whether one ``extra_modes`` entry is a backend name with a list or tuple of mode ids."""
    return isinstance(name, str) and isinstance(ids, list | tuple) and all(isinstance(mode, str) for mode in ids)


def checked_prefer(prefer: object) -> Prefer | None:
    """A router's ``prefer``: what it ranks by, and asks or sends as the request's ``prefer``.

    Raises:
        ConfigurationError: for anything but ``None`` or one of :data:`Prefer`'s values, such as ``"cheapest"``.
    """
    known = get_args(Prefer)
    if prefer is None or (isinstance(prefer, str) and prefer in known):
        return cast("Prefer | None", prefer)
    raise ConfigurationError(MSG_PREFER.format(known=", ".join(map(repr, known)), got=prefer))

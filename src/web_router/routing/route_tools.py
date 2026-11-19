"""Which tools ask the attached router, and whether it is shown vendor modes: the caller's choice, else the router's."""

from __future__ import annotations

from collections.abc import Collection
from typing import get_args

from web_router.constants_invoke import (
    MSG_ROUTE_MODES,
    MSG_ROUTE_TOOLS,
    MSG_ROUTER_ROUTE_MODES,
    MSG_ROUTER_ROUTE_TOOLS,
    ROUTER_ROUTE_MODES_ATTR,
    ROUTER_ROUTE_TOOLS_ATTR,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import ToolName

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

"""Attaching and calling a router: its default time limit, the tools it is consulted on, and the messages.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

ROUTER_TIMEOUT_S = 0.05
"""Seconds a router has to answer when it declares no ``timeout_s``: 50 ms.

The library cannot tell a local rule from a model call, so a slower router
declares its own ``timeout_s`` -- the examples do.
"""

ROUTER_TIMEOUT_ATTR = "timeout_s"
"""The attribute, on a router function or object, that declares its time limit in seconds."""

ROUTER_THREAD_NAME = "web-router-router"
"""The name of the worker thread a sync router runs on, and of the loop thread an async one runs on from sync code."""


MSG_ROUTER_FORM = (
    "router= takes a function, a coroutine function, or an object with route(req) or aroute(req); got {kind}."
)
MSG_ROUTER_CLASS = "router= got the class {name!r}; pass an instance of it."
MSG_ROUTER_TIMEOUT_S = "A router's timeout_s must be a positive number of seconds; got {value!r}."
MSG_ROUTE_TOOLS = "route_tools takes a collection of tool names from {known}; got {got!r}."
MSG_ROUTER_FAILED = (
    "Router {router!r} failed ({code}); fail_on_router_error=True raises rather than use the static order."
)
MSG_ROUTER_OTHER_LOOP = "The router answered with a future bound to another event loop, which this call cannot await."

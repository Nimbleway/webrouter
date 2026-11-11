"""Reading a router's form, its declared time limit and the name a decision record gives it.

Experimental. See :mod:`web_router.routing.invoke`.
"""

from __future__ import annotations

import functools
import inspect
import math

from web_router.constants_invoke import MSG_ROUTER_TIMEOUT_S, ROUTER_NAME_ATTR, ROUTER_TIMEOUT_ATTR, ROUTER_TIMEOUT_S
from web_router.exceptions import ConfigurationError
from web_router.routing._runner import _Side


def _sides(router: object) -> tuple[_Side | None, _Side | None]:
    """The router's sync and async sides: a function, method or partial, or an object's ``route`` and ``aroute``."""
    route = getattr(router, "route", None)
    aroute = getattr(router, "aroute", None)
    if callable(route) or callable(aroute):
        sync_fn = route if callable(route) and not inspect.iscoroutinefunction(route) else None
        async_fn = aroute if callable(aroute) else (route if callable(route) and sync_fn is None else None)
        return sync_fn, async_fn
    if not (inspect.isroutine(router) or isinstance(router, functools.partial)):
        return None, None  # a string, or an object that is only callable: neither form
    return (None, router) if inspect.iscoroutinefunction(router) else (router, None)


def _declared_timeout(router: object) -> float:
    declared = getattr(router, ROUTER_TIMEOUT_ATTR, None)
    if declared is None:
        return ROUTER_TIMEOUT_S
    if isinstance(declared, bool) or not isinstance(declared, (int, float)) or not 0 < declared < math.inf:
        raise ConfigurationError(MSG_ROUTER_TIMEOUT_S.format(value=declared))
    return float(declared)


def _name_of(router: object) -> str:
    """What a decision record calls the router: its ``router_name`` when it sets one, else its qualified name."""
    declared = getattr(router, ROUTER_NAME_ATTR, None)
    if isinstance(declared, str) and declared:
        return declared
    return getattr(router, "__qualname__", None) or type(router).__qualname__

"""The seam in ``auto``'s fallback loops: a per-call order, and a hook before each attempt.

Experimental. Every ``auto`` chain that can be routed -- search, extract,
answer, research and browse -- passes the members eligible for a request
through :func:`in_call_order` and asks :func:`admitted` before it tries each
one, and tells it through :func:`failed` when a member fails. With no
:class:`CallRoute` in force, as on every call today, the order is the
configured chain and every member is admitted, so ``auto`` behaves exactly as
it always has.

A route is held in a context variable, as the per-call deadline is, because it
belongs to the call and not to the request, and would otherwise have to be
threaded through every provider between the router and the chain.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from web_router.routing.contract import ToolName
from web_router.types import NormalizedRequest


@dataclass(frozen=True, kw_only=True)
class Attempt:
    """One member ``auto`` is about to try."""

    tool: ToolName
    """The method being called."""

    provider: str
    """The member, by registry name."""

    request: NormalizedRequest
    """The request the member would be sent."""


AttemptHook = Callable[[Attempt], bool]
"""Called before each attempt; ``False`` passes over the member without calling it."""

FailureHook = Callable[[ToolName, str, BaseException], None]
"""Called when a member fails and ``auto`` moves on: the method, the member's name, and the failure."""


@dataclass(frozen=True, kw_only=True)
class CallRoute:
    """How one call's ``auto`` chains run: the order to try members in, and the hook before each."""

    order: Mapping[ToolName, Sequence[str]] = field(default_factory=dict)
    """Per method, the members to try, by name, in order. A member left out is not tried; a method
    left out keeps its configured chain."""

    before_attempt: AttemptHook | None = None
    """Called before each attempt. ``None`` admits every member."""

    on_failure: FailureHook | None = None
    """Called when a member fails and the chain moves on. ``None`` notes nothing."""


_ROUTE: ContextVar[CallRoute | None] = ContextVar("web_router_call_route", default=None)


@contextmanager
def call_route(route: CallRoute) -> Iterator[None]:
    """Hold ``route`` over the calls made inside the block."""
    token = _ROUTE.set(route)
    try:
        yield
    finally:
        _ROUTE.reset(token)


def current_route() -> CallRoute | None:
    """The route in force for the call in flight, or ``None`` when it has none."""
    return _ROUTE.get()


def in_force_with(route: CallRoute) -> CallRoute:
    """``route``'s order, kept with the hook already in force -- the budget's -- so both hold."""
    in_force = current_route()
    return route if in_force is None else replace(in_force, order={**in_force.order, **route.order})


def in_route(route: CallRoute | None) -> AbstractContextManager[None]:
    """``route`` in force beside the hook already in force; nothing for a call no router ordered."""
    return nullcontext() if route is None else call_route(in_force_with(route))


def in_call_order(tool: ToolName, eligible: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """``eligible`` -- ``(position, name)`` pairs in chain order -- in the call's order for ``tool``.

    Members that share a name keep their chain order among themselves.
    """
    route = _ROUTE.get()
    if route is None or tool not in route.order:
        return eligible
    rank = {
        name: index for index, name in enumerate(dict.fromkeys(route.order[tool]))
    }  # a repeat keeps its first place
    return sorted((member for member in eligible if member[1] in rank), key=lambda member: rank[member[1]])


def admitted(tool: ToolName, provider: str, request: NormalizedRequest) -> bool:
    """Whether the call's hook lets ``auto`` try ``provider`` next. With no hook, always."""
    route = _ROUTE.get()
    if route is None or route.before_attempt is None:
        return True
    return route.before_attempt(Attempt(tool=tool, provider=provider, request=request))


def failed(failures: dict[str, Any], tool: ToolName, provider: str, exc: BaseException) -> None:
    """Note in ``failures`` that ``provider`` failed with ``exc``, and tell the call's failure hook, if any."""
    failures[provider] = exc
    noted(tool, provider, exc)


def noted(tool: ToolName, provider: str, exc: BaseException) -> None:
    """Tell the call's failure hook, if any, that ``exc`` moved ``auto`` past ``provider``: a deadline, a refusal."""
    route = _ROUTE.get()
    if route is not None and route.on_failure is not None:
        route.on_failure(tool, provider, exc)

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

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from web_router.constants_errors import MSG_KEY_REFUSED
from web_router.routing.contract import ToolName, candidate_id
from web_router.routing.entry_names import split_entry
from web_router.types import NormalizedRequest
from web_router.types_errors import CHAIN_STOPPING_KINDS, ERROR_KIND_AUTH

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Attempt:
    """One member ``auto`` is about to try."""

    tool: ToolName
    """The method being called."""

    provider: str
    """The member, by registry name."""

    request: NormalizedRequest
    """The request the member would be sent: in :attr:`mode`, when it has one."""

    mode: str | None = None
    """The vendor mode a router's ``attempts`` named for it; ``None`` for the backend's default."""

    @property
    def id(self) -> str:
        """The candidate it is, by id: ``search:exa``, or with its mode ``search:exa:exa-instant``."""
        return candidate_id(self.tool, self.provider, self.mode)


AttemptHook = Callable[[Attempt], bool]
"""Called before each attempt; ``False`` passes over the member without calling it."""

FailureHook = Callable[[ToolName, str, BaseException | str], None]
"""Called when ``auto`` moves past a member: the method, its name, and the failure, or a passed-over one's hop code."""

RefundHook = Callable[[Attempt], None]
"""Called when a backend refused an attempt's vendor mode: the attempt is taken back, as never charged."""

AnswerHook = Callable[[ToolName, str], None]
"""Called when a member read part of a call's answer though another's response carries it: the method, the name."""


@dataclass(frozen=True, kw_only=True)
class CallRoute:
    """How one call's ``auto`` chains run: the order to try members in, and the hook before each."""

    order: Mapping[ToolName, Sequence[str]] = field(default_factory=dict)
    """Per method, the members to try, by name, in order. A member left out is not tried; a method
    left out keeps its configured chain. ``exa:exa-instant`` names member ``exa`` in that vendor mode, unless a
    member has that name itself."""

    router_params: frozenset[str] = frozenset()
    """The options a router set on the request: an attempt's vendor mode replaces ``search_depth`` and ``effort``."""

    before_attempt: AttemptHook | None = None
    """Called before each attempt. ``None`` admits every member."""

    on_failure: FailureHook | None = None
    """Called when a member fails and the chain moves on. ``None`` notes nothing."""

    on_answer: AnswerHook | None = None
    """Called when a member ``auto`` retried a batch's failed URLs on read some of them. ``None`` notes nothing."""

    on_refund: RefundHook | None = None
    """Called when a backend refused an attempt's vendor mode. ``None`` takes nothing back."""


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
    if in_force is None:
        return route
    return replace(in_force, order={**in_force.order, **route.order}, router_params=route.router_params)


def in_route(route: CallRoute | None) -> AbstractContextManager[None]:
    """``route`` in force beside the hook already in force; nothing for a call no router ordered."""
    return nullcontext() if route is None else call_route(in_force_with(route))


def in_call_order(tool: ToolName, eligible: list[tuple[int, str]]) -> list[tuple[int, str, str | None]]:
    """``eligible`` -- ``(position, name)`` pairs in chain order -- in the call's order for ``tool``, each with the
    vendor mode to run it in (``None``, its default).

    Members that share a name keep their chain order among themselves. A repeated name keeps its first place; a
    member the order names in several modes runs in each, in the order's.
    """
    route = _ROUTE.get()
    if route is None or tool not in route.order:
        return [(position, name, None) for position, name in eligible]
    names = {name for _, name in eligible}
    ordered: list[tuple[int, str, str | None]] = []
    for entry in dict.fromkeys(route.order[tool]):
        name, mode = split_entry(entry, names)
        ordered.extend((position, name, mode) for position, member in eligible if member == name)
    return ordered


def admitted(tool: ToolName, provider: str, request: NormalizedRequest) -> bool:
    """Whether the call's hook lets ``auto`` try ``provider`` next. With no hook, always."""
    return admits(Attempt(tool=tool, provider=provider, request=request))


def admits(attempt: Attempt) -> bool:
    """Whether the call's hook lets ``auto`` make ``attempt`` next. With no hook, always."""
    route = _ROUTE.get()
    return route is None or route.before_attempt is None or route.before_attempt(attempt)


def refunded(attempt: Attempt) -> None:
    """Tell the call's refund hook, if any, that a backend refused ``attempt``'s vendor mode."""
    route = _ROUTE.get()
    if route is not None and route.on_refund is not None:
        route.on_refund(attempt)


def failed(failures: dict[str, Any], tool: ToolName, provider: str, exc: BaseException, member: object = None) -> None:
    """Note in ``failures`` that ``provider`` failed with ``exc``, and tell the call's failure hook, if any.

    ``member`` is the provider object that raised, when built: it says whether it held a credential to refuse.
    Raises ``exc`` itself when no other backend could do better: a ``local``
    failure (a closed event loop, a provider's own bug) would fail every
    member the same way, and a ``target`` one (a host that does not resolve,
    an invalid URL) is the page's, not the backend's. Moving on would only
    bill more calls and hide the cause.
    """
    failures[provider] = exc
    noted(tool, provider, exc)
    warn_if_refused(provider, exc, member)
    if getattr(exc, "kind", None) in CHAIN_STOPPING_KINDS:
        raise exc


def warn_if_refused(provider: str, exc: BaseException, member: object = None) -> None:
    """Log a WARNING when ``exc`` is ``provider`` refusing its key: a setup problem moving on would otherwise hide.

    A ``member`` that sent no credential (a keyless tier, a keyless custom provider) refused no key of the
    caller's: its 401 or 403 is left to the hop's INFO record.
    """
    if getattr(exc, "kind", None) == ERROR_KIND_AUTH and getattr(member, "holds_credential", True):
        logger.warning(MSG_KEY_REFUSED, provider, getattr(exc, "status_code", None))


def noted(tool: ToolName, provider: str, exc: BaseException | str) -> None:
    """Tell the call's failure hook, if any, that ``exc`` moved ``auto`` past ``provider``: a deadline, a refusal."""
    route = _ROUTE.get()
    if route is not None and route.on_failure is not None:
        route.on_failure(tool, provider, exc)


def contributed(tool: ToolName, provider: str) -> None:
    """Tell the call's answer hook, if any, that ``provider`` read part of an answer another member's carries."""
    route = _ROUTE.get()
    if route is not None and route.on_answer is not None:
        route.on_answer(tool, provider)

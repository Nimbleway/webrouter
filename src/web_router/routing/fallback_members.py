"""The ``fallback`` router's members: each built or taken as given, attached, named, and closed when built here.

Experimental. See :mod:`web_router.routing.fallback`. A member is a registered
router name, a mapping of that name (``"name"``) and its config, or a router
function or object. One built by name is the ``fallback`` router's to close, as
a router built by name is its ``WebRouter``'s; one passed in is never closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from web_router.constants_fallback import (
    FALLBACK_CUSTOM_NAME,
    FALLBACK_NAME_KEY,
    MSG_FALLBACK_MEMBER,
    MSG_FALLBACK_ROUTERS,
)
from web_router.constants_invoke import ROUTER_NAME_ATTR, ROUTER_URL_POLICY_ATTR
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import ToolName
from web_router.routing.invoke import AttachedRouter, RouterLike
from web_router.routing.registry import aclose_router, discard_router, resolve_router
from web_router.routing.route_tools import checked_route_tools
from web_router.routing.router_owner import _discard_refused
from web_router.routing.url_policy import url_check


@dataclass(frozen=True)
class Member:
    """One router the ``fallback`` router asks: its name, its attached form, and the router when built here."""

    name: str
    attached: AttachedRouter
    owned: RouterLike | None
    """The router when it was built by name, so the ``fallback`` router closes it; ``None`` when passed in."""
    tools: frozenset[ToolName]
    """The tools it is asked on: the ``route_tools`` it declares, else all five."""
    url_policy: str | None
    """Its ``min_url_policy``, when it declares one."""


def _spec(index: int, spec: object) -> tuple[Any, dict[str, Any] | None]:
    """The router or name a member gives, and the config to build it with by name."""
    if isinstance(spec, Mapping):
        config = dict(spec)
        name = config.pop(FALLBACK_NAME_KEY, None)
        if not isinstance(name, str):
            raise ConfigurationError(MSG_FALLBACK_MEMBER.format(index=index, kind="a mapping without a string 'name'"))
        return name, config
    if spec is None or isinstance(spec, bool | int | float | bytes):
        raise ConfigurationError(MSG_FALLBACK_MEMBER.format(index=index, kind=type(spec).__name__))
    return spec, None


def _name(router: RouterLike, by_name: str | None) -> str:
    """A member's name: the ``router_name`` it sets, else its registered name, else ``custom`` -- never its path."""
    declared = getattr(router, ROUTER_NAME_ATTR, None)
    if isinstance(declared, str) and declared:
        return declared
    return by_name or FALLBACK_CUSTOM_NAME


def _member(index: int, spec: object) -> Member:
    """One member, attached; one built by name and then refused is closed before the refusal propagates."""
    router, config = _spec(index, spec)
    built, owned = resolve_router(router, config)
    assert built is not None  # None never reaches here: _spec refuses it
    try:
        attached = AttachedRouter(built, fail_on_error=False)
        tools = checked_route_tools(None, built)
        policy = getattr(built, ROUTER_URL_POLICY_ATTR, None)  # a property may raise: the router is closed too
        url_check(policy)  # refused as attaching it directly refuses it
    except BaseException:
        _discard_refused(owned)
        raise
    by_name = router.strip() if isinstance(router, str) else None
    return Member(_name(built, by_name), attached, owned, tools, policy if isinstance(policy, str) else None)


def built_members(routers: object) -> list[Member]:
    """Every member of ``routers``, in order; when one is refused, those already built here are closed.

    Raises:
        ConfigurationError: for ``routers`` that is not a non-empty list, a member in no form, or one that
            :func:`~web_router.routing.registry.resolve_router` or :class:`AttachedRouter` refuses.
    """
    if isinstance(routers, str | bytes | Mapping) or not isinstance(routers, Sequence) or not routers:
        raise ConfigurationError(MSG_FALLBACK_ROUTERS.format(got=type(routers).__name__))
    members: list[Member] = []
    try:
        for index, spec in enumerate(routers):
            members.append(_member(index, spec))
    except BaseException:
        for member in members:
            _discard_refused(member.owned)
        raise
    return members


def close_members(members: Sequence[Member]) -> None:
    """Close every member built here, from sync code; the first close that fails is raised once all have run."""
    failed: Exception | None = None
    for member in members:
        try:
            discard_router(member.owned)
        except Exception as exc:
            failed = failed or exc
    if failed is not None:
        raise failed


async def aclose_members(members: Sequence[Member]) -> None:
    """:func:`close_members` from async code: each member's ``aclose()``, else its ``close()``."""
    failed: Exception | None = None
    for member in members:
        try:
            await aclose_router(member.owned)
        except Exception as exc:
            failed = failed or exc
    if failed is not None:
        raise failed

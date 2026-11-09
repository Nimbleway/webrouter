"""A routed call: when to ask the attached router, what it is shown, and how its answer orders the call.

Experimental. The routers take :class:`SyncRouted` or :class:`AsyncRouted` as
a mixin, and every method that can be routed -- search, extract, answer,
research and browse -- runs through ``_routed`` / ``_arouted``. The router is
asked only when one is attached, the method is in ``route_tools``, and the
call goes to ``auto``: a provider the caller names, or a default that is not
``auto``, is a pinned provider and leaves nothing to order. With no router
attached a call runs exactly as it did before routers existed.

The router orders the backends of the method the caller called; it never
changes the method. It is shown the candidates
:func:`~web_router.routing.candidates.build_candidates` finds on the chain the
call runs on, and what it answered is merged by
:func:`~web_router.routing.decide.decided` -- the merge rule, then the
budget's research effort -- and recorded on the response's ``decision``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeVar, get_args

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_invoke import MSG_ROUTE_TOOLS, ROUTER_URL_POLICY_ATTR
from web_router.exceptions import ConfigurationError
from web_router.routing.attempts import CallRoute, in_route
from web_router.routing.budget import BudgetedCall, budget_scope, recorded
from web_router.routing.candidates import REQUEST_OF, CandidateSet, build_candidates
from web_router.routing.contract import Budget, RouteRequest, ToolName
from web_router.routing.decide import decided
from web_router.routing.effort_call import Only, named, unrouted, with_record
from web_router.routing.invoke import AttachedRouter, RouterLike, RouterOutcome
from web_router.routing.merge_urls import UrlCheck
from web_router.routing.pool import limited
from web_router.routing.record import prompt_of
from web_router.routing.registry import aclose_router, discard_router, resolve_router
from web_router.routing.url_policy import UrlPolicy, url_check
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)

_TOOL_OF: dict[type[NormalizedRequest], ToolName] = {cls: tool for tool, cls in REQUEST_OF.items()}
"""The tool a request is for, read from its type."""


_ALL_TOOLS: frozenset[ToolName] = frozenset(get_args(ToolName))


def _route_tools(route_tools: Collection[ToolName] | None) -> frozenset[ToolName]:
    """The tools that ask the router: ``route_tools``, checked, or all five when it is ``None``."""
    if route_tools is None:
        return _ALL_TOOLS
    if isinstance(route_tools, str) or not set(route_tools) <= _ALL_TOOLS:
        known = ", ".join(get_args(ToolName))
        raise ConfigurationError(MSG_ROUTE_TOOLS.format(known=known, got=route_tools))
    return frozenset(route_tools)


class RouterSetup:
    """The attached router and the tools it is consulted on, shared by both routers."""

    _router: AttachedRouter | None = None
    _owned_router: RouterLike | None = None
    _route_tools: frozenset[ToolName] = _ALL_TOOLS
    _url_check: UrlCheck | None = None
    _router_url_check: UrlCheck | None = None
    """The check on the URLs a router sets: the caller's ``url_policy``, else the router's ``min_url_policy``."""

    def _attach_router(
        self,
        router: RouterLike | str | None,
        route_tools: Collection[ToolName] | None,
        fail_on_router_error: bool,
        url_policy: UrlPolicy | None = None,
        router_config: Mapping[str, Any] | None = None,
    ) -> None:
        """Attach the router; called last in a constructor, since a router built by name is the caller's to close.

        Raises:
            ConfigurationError: for a ``router`` in neither form nor a registered name, ``router_config`` without a
                name, a ``route_tools`` naming something else, or an unknown ``url_policy``.
        """
        self._url_check = url_check(url_policy)
        self._route_tools = _route_tools(route_tools)
        attached, owned = resolve_router(router, router_config)
        try:
            self._router = None if attached is None else AttachedRouter(attached, fail_on_error=fail_on_router_error)
            floor = getattr(attached, ROUTER_URL_POLICY_ATTR, None) if url_policy is None else url_policy
            self._router_url_check = url_check(floor)
        except BaseException:
            discard_router(owned)  # built here and refused: nothing else would close it
            raise
        self._owned_router = owned

    def _close_router(self) -> None:
        """Close the router when it was built here by name; one passed in is the caller's to close."""
        discard_router(self._owned_router)  # an async-only router's aclose(), run here, included

    async def _aclose_router(self) -> None:
        """:meth:`_close_router` from async code: the router's ``aclose()``, else its ``close()``."""
        await aclose_router(self._owned_router)

    def _route_request(
        self, chain: Any, request: NormalizedRequest, budget: Budget | None
    ) -> tuple[RouteRequest, CandidateSet] | None:
        """What the router is shown for this call, and the candidates; ``None`` when the call is not routed."""
        tool = _TOOL_OF.get(type(request))
        if self._router is None or tool is None or tool not in self._route_tools:
            return None
        if getattr(chain, "name", None) != PROVIDER_AUTO:
            return None  # a named backend: nothing to order
        found = build_candidates(Only(tool, chain), {tool: request}, budget=budget)
        if not found.candidates:
            return None
        prompt, urls = prompt_of(tool, request)
        req = RouteRequest(
            prompt=prompt,
            candidates=found.candidates,
            deadline_s=self._router.deadline(budget),
            urls=urls,
            budget=budget,
            pinned_tool=tool,
        )
        return req, found

    def _attached(self) -> AttachedRouter:
        assert self._router is not None  # a call is routed only with a router attached
        return self._router

    def _prepared(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        routed: tuple[RouteRequest, CandidateSet] | None,
        outcome: RouterOutcome | None,
    ) -> tuple[CallRoute | None, _RequestT, DecisionRecord | None]:
        """The order in force, the request and the record: the router's merged, or with none the budget's effort."""
        if routed is None or outcome is None:
            request, mapped = unrouted(chain, request, budget)
            return None, request, mapped
        return self._merged(routed, outcome, request)

    def _merged(
        self, routed: tuple[RouteRequest, CandidateSet], outcome: RouterOutcome, request: _RequestT
    ) -> tuple[CallRoute, _RequestT, DecisionRecord]:
        """The order the call runs in, its request with the options a router or the budget set, and the record."""
        req, found = routed
        caller_set = request.specified_parameters() | request.always_sent
        out = decided(
            req, found, outcome, router=self._attached().name, caller_set=caller_set, url_check=self._router_url_check
        )
        if out.params:
            request = request.model_copy(update=dict(out.params))
        return CallRoute(order={out.tool: out.providers}), request, out.record


@contextmanager
def _logged(scope: BudgetedCall[Any], route: CallRoute | None) -> Iterator[BudgetedCall[Any]]:
    """``route`` in force beside the budget's hook, and a ledger logging its attempts; nothing for an unrouted call."""
    if route is None:
        yield scope
        return
    with recorded(scope) as logged, in_route(route):
        yield logged


class SyncRouted(RouterSetup):
    def _routed(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], _ResponseT],
    ) -> _ResponseT:
        """Run ``call``, which calls ``chain``, in ``budget``'s scope.

        A routed call runs in the order the router chose, and its response records how.
        """
        request, chosen = named(chain, request, limited(budget, self))  # before the scope, which admits a named one
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request, self) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else self._attached().call(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with _logged(scope, route) as logged:
                return logged.settle(with_record(call(request), record or chosen))


class AsyncRouted(RouterSetup):
    async def _arouted(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], Awaitable[_ResponseT]],
    ) -> _ResponseT:
        """Await ``call``, which calls ``chain``, as :meth:`SyncRouted._routed` runs it."""
        request, chosen = named(chain, request, limited(budget, self))  # before the scope, which admits a named one
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request, self) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else await self._attached().acall(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with _logged(scope, route) as logged:
                return logged.settle(with_record(await call(request), record or chosen))

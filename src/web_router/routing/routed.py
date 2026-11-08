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

from collections.abc import Awaitable, Callable, Collection
from typing import Any, TypeVar, get_args

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_invoke import MSG_ROUTE_TOOLS
from web_router.exceptions import ConfigurationError
from web_router.routing.attempts import CallRoute, in_route
from web_router.routing.budget import budget_scope
from web_router.routing.candidates import REQUEST_OF, CandidateSet, build_candidates
from web_router.routing.contract import Budget, RouteRequest, ToolName
from web_router.routing.decide import decided
from web_router.routing.effort_call import Only, named, unrouted, with_record
from web_router.routing.invoke import AttachedRouter, RouterLike, RouterOutcome
from web_router.routing.merge_urls import UrlCheck
from web_router.routing.url_policy import UrlPolicy, url_check
from web_router.types import DecisionRecord, NormalizedRequest

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


def _prompt(tool: ToolName, request: Any) -> tuple[str, tuple[str, ...]]:
    """What the router reads for ``request``: the query or task, or for extract the URLs one per line; and its URLs."""
    if tool == "extract":
        return "\n".join(request.urls), tuple(request.urls)
    if tool == "browse":
        return request.task, (request.start_url,) if request.start_url else ()
    return request.query, ()


class RouterSetup:
    """The attached router and the tools it is consulted on, shared by both routers."""

    _router: AttachedRouter | None = None
    _route_tools: frozenset[ToolName] = _ALL_TOOLS
    _url_check: UrlCheck | None = None

    def _attach_router(
        self,
        router: RouterLike | None,
        route_tools: Collection[ToolName] | None,
        fail_on_router_error: bool,
        url_policy: UrlPolicy | None = None,
    ) -> None:
        """
        Raises:
            ConfigurationError: for a ``router`` in neither form, a ``route_tools`` naming something else, or an
                unknown ``url_policy``.
        """
        self._url_check = url_check(url_policy)
        self._route_tools = _route_tools(route_tools)
        self._router = None if router is None else AttachedRouter(router, fail_on_error=fail_on_router_error)

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
        prompt, urls = _prompt(tool, request)
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
            req, found, outcome, router=self._attached().name, caller_set=caller_set, url_check=self._url_check
        )
        if out.params:
            request = request.model_copy(update=dict(out.params))
        return CallRoute(order={out.tool: out.providers}), request, out.record


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
        request, chosen = named(chain, request, budget)  # before the scope, which admits a named backend
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request) as scope:
            routed = self._route_request(chain, request, budget)
            outcome = None if routed is None else self._attached().call(routed[0])
            route, request, record = self._prepared(chain, request, budget, routed, outcome)
            with in_route(route):
                return scope.settle(with_record(call(request), record or chosen))


class AsyncRouted(RouterSetup):
    async def _arouted(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], Awaitable[_ResponseT]],
    ) -> _ResponseT:
        """Await ``call``, which calls ``chain``, as :meth:`SyncRouted._routed` runs it."""
        request, chosen = named(chain, request, budget)  # before the scope, which admits a named backend
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request) as scope:
            routed = self._route_request(chain, request, budget)
            outcome = None if routed is None else await self._attached().acall(routed[0])
            route, request, record = self._prepared(chain, request, budget, routed, outcome)
            with in_route(route):
                return scope.settle(with_record(await call(request), record or chosen))

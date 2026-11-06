"""A routed call: when to ask the attached router, what it is shown, and how its answer orders the call.

Experimental. The routers take :class:`SyncRouted` or :class:`AsyncRouted` as
a mixin, and every method that can be routed -- search, extract, answer,
research and browse -- runs through ``_routed`` / ``_arouted``. The router is
asked only when one is attached, the method is in ``route_tools``, and the
call goes to ``auto``: a provider the caller names, or a default that is not
``auto``, is a pinned provider and leaves nothing to order. With no router
attached a call runs exactly as it did before routers existed.

The router orders the backends of the method the caller called; it never
changes the method. What it answered is applied by
:func:`~web_router.routing.interim.ranked_first` until the merge rule lands,
and recorded on the response's ``decision``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import replace
from typing import Any, TypeVar, get_args

from pydantic import BaseModel

from web_router.constants_invoke import MSG_ROUTE_TOOLS, ROUTER_TIMEOUT
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.exceptions import ConfigurationError
from web_router.routing.attempts import CallRoute, call_route, current_route
from web_router.routing.budget import budget_scope
from web_router.routing.contract import Budget, RouteRequest, ToolName
from web_router.routing.interim import chain_candidates, ranked_first
from web_router.routing.invoke import AttachedRouter, RouterLike, RouterOutcome
from web_router.types import (
    BrowseRequest,
    DecisionRecord,
    ExtractRequest,
    NormalizedRequest,
    ResearchRequest,
    SearchRequest,
)
from web_router.types_answer import AnswerRequest

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)

_TOOL_OF: dict[type[NormalizedRequest], ToolName] = {
    SearchRequest: "search",
    ExtractRequest: "extract",
    AnswerRequest: "answer",
    ResearchRequest: "research",
    BrowseRequest: "browse",
}
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


def _ids(tool: ToolName, providers: Collection[str]) -> list[str]:
    return [f"{tool}{CANDIDATE_ID_SEPARATOR}{name}" for name in providers]


class RouterSetup:
    """The attached router and the tools it is consulted on, shared by both routers."""

    _router: AttachedRouter | None = None
    _route_tools: frozenset[ToolName] = _ALL_TOOLS

    def _attach_router(
        self, router: RouterLike | None, route_tools: Collection[ToolName] | None, fail_on_router_error: bool
    ) -> None:
        """
        Raises:
            ConfigurationError: for a ``router`` in neither form, or a ``route_tools`` naming something else.
        """
        self._route_tools = _route_tools(route_tools)
        self._router = None if router is None else AttachedRouter(router, fail_on_error=fail_on_router_error)

    def _route_request(self, chain: Any, request: NormalizedRequest, budget: Budget | None) -> RouteRequest | None:
        """What the router is shown for this call, or ``None`` when the call is not routed."""
        tool = _TOOL_OF.get(type(request))
        if self._router is None or tool is None or tool not in self._route_tools:
            return None
        candidates = chain_candidates(tool, chain, request)
        if not candidates:
            return None
        prompt, urls = _prompt(tool, request)
        return RouteRequest(
            prompt=prompt,
            candidates=candidates,
            deadline_s=self._router.deadline(budget),
            urls=urls,
            budget=budget,
            pinned_tool=tool,
        )

    def _merged(self, req: RouteRequest, outcome: RouterOutcome) -> tuple[CallRoute, DecisionRecord]:
        """The order the call runs in, and the record of how it was chosen."""
        assert self._router is not None and req.pinned_tool is not None  # set on every routed call
        tool, decision = req.pinned_tool, outcome.decision
        merged = ranked_first(tool, req.candidates, decision)
        ranked = (decision.providers or {}).get(tool) if decision is not None else None
        record = DecisionRecord(
            router=self._router.name,
            offered=[candidate.id for candidate in req.candidates],
            ranked=_ids(tool, ranked) if ranked is not None else None,
            merged=_ids(tool, merged),
            reason=outcome.failure or (decision.reason if decision is not None else None),
            router_timeout=outcome.failure == ROUTER_TIMEOUT,
            router_ms=outcome.elapsed_ms,
        )
        return CallRoute(order={tool: merged}), record


def _in_force_with(route: CallRoute) -> CallRoute:
    """``route``'s order, kept with the hook already in force -- the budget's -- so both hold."""
    in_force = current_route()
    return route if in_force is None else replace(in_force, order={**in_force.order, **route.order})


class SyncRouted(RouterSetup):
    def _routed(
        self,
        chain: Any,
        request: NormalizedRequest,
        budget: Budget | None,
        call: Callable[[], _ResponseT],
    ) -> _ResponseT:
        """Run ``call``, which calls ``chain``, in ``budget``'s scope.

        A routed call runs in the order the router chose, and its response records how.
        """
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request) as scope:
            req = self._route_request(chain, request, budget)
            if req is None:
                return scope.settle(call())
            assert self._router is not None  # a request is built only with a router attached
            route, record = self._merged(req, self._router.call(req))
            with call_route(_in_force_with(route)):
                response = call()
            return scope.settle(response.model_copy(update={"decision": record}))


class AsyncRouted(RouterSetup):
    async def _arouted(
        self,
        chain: Any,
        request: NormalizedRequest,
        budget: Budget | None,
        call: Callable[[], Awaitable[_ResponseT]],
    ) -> _ResponseT:
        """Await ``call``, which calls ``chain``, as :meth:`SyncRouted._routed` runs it."""
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request) as scope:
            req = self._route_request(chain, request, budget)
            if req is None:
                return scope.settle(await call())
            assert self._router is not None  # a request is built only with a router attached
            route, record = self._merged(req, await self._router.acall(req))
            with call_route(_in_force_with(route)):
                response = await call()
            return scope.settle(response.model_copy(update={"decision": record}))

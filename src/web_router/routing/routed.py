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

from collections.abc import Awaitable, Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, TypeVar, get_args

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import AUTO_GETTERS
from web_router.constants_invoke import MSG_ROUTE_TOOLS
from web_router.constants_params import PARAMS_SOURCE_BUDGET
from web_router.exceptions import ConfigurationError
from web_router.routing.attempts import CallRoute, call_route, current_route
from web_router.routing.budget import BudgetedCall, budget_scope, recorded
from web_router.routing.candidates import CandidateSet, build_candidates
from web_router.routing.contract import Budget, RouteRequest, ToolName
from web_router.routing.decide import budget_effort, decided
from web_router.routing.invoke import AttachedRouter, RouterLike, RouterOutcome
from web_router.routing.merge import UrlCheck
from web_router.routing.record import prompt_of
from web_router.routing.url_policy import UrlPolicy, url_check
from web_router.types import (
    BrowseRequest,
    ExtractRequest,
    NormalizedRequest,
    ResearchRequest,
    SearchRequest,
)
from web_router.types_answer import AnswerRequest
from web_router.types_decision import DecisionRecord

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)

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


class _Only:
    """A router whose ``auto`` for ``tool`` is ``chain``: the candidates of the chain the call runs on."""

    def __init__(self, tool: ToolName, chain: Any) -> None:
        setattr(self, AUTO_GETTERS[tool], lambda _name: chain)


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
        found = build_candidates(_Only(tool, chain), {tool: request}, budget=budget)
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
            request, mapped = _unrouted(chain, request, budget)
            return None, request, mapped
        return self._merged(routed, outcome, request)

    def _merged(
        self, routed: tuple[RouteRequest, CandidateSet], outcome: RouterOutcome, request: _RequestT
    ) -> tuple[CallRoute, _RequestT, DecisionRecord]:
        """The order the call runs in, its request with the options a router or the budget set, and the record."""
        assert self._router is not None  # set on every routed call
        req, found = routed
        caller_set = request.specified_parameters() | request.always_sent
        out = decided(req, found, outcome, router=self._router.name, caller_set=caller_set, url_check=self._url_check)
        if out.params:
            request = request.model_copy(update=dict(out.params))
        return CallRoute(order={out.tool: out.providers}), request, out.record


def _unrouted(chain: Any, request: _RequestT, budget: Budget | None) -> tuple[_RequestT, DecisionRecord | None]:
    """A call no router orders: with a budget, research still runs at the effort the budget maps.

    The effort is the one :func:`~web_router.routing.decide.budget_effort` picks over the backends the budget
    leaves in the chain's order -- one that fits on every one of them -- unless the caller set one.
    """
    capped = budget is not None and (budget.max_cost_usd is not None or budget.time_limit_s is not None)
    if not capped or not isinstance(request, ResearchRequest) or getattr(chain, "name", None) != PROVIDER_AUTO:
        return request, None
    found = build_candidates(_Only("research", chain), {"research": request}, budget=budget)
    params: dict[str, Any] = {}
    caller_set = request.specified_parameters()
    choice = budget_effort("research", tuple(c.provider for c in found.candidates), params, caller_set, budget)
    if choice is None:
        return request, None
    if choice.effort is None:
        return request, DecisionRecord(params_reason=choice.reason)
    record = DecisionRecord(params=dict(params), params_source=PARAMS_SOURCE_BUDGET, params_reason=choice.reason)
    return request.model_copy(update=params), record


def _decided(response: _ResponseT, record: DecisionRecord | None) -> _ResponseT:
    return response if record is None else response.model_copy(update={"decision": record})


@contextmanager
def _logged(scope: BudgetedCall[Any], route: CallRoute | None) -> Iterator[BudgetedCall[Any]]:
    """``route`` in force beside the budget's hook, and a ledger logging its attempts; nothing for an unrouted call."""
    if route is None:
        yield scope
        return
    with recorded(scope) as logged, call_route(in_force_with(route)):
        yield logged


def in_force_with(route: CallRoute) -> CallRoute:
    """``route``'s order, kept with the hook already in force -- the budget's -- so both hold."""
    in_force = current_route()
    return route if in_force is None else replace(in_force, order={**in_force.order, **route.order})


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
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else self._attached().call(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with _logged(scope, route) as logged:
                return logged.settle(_decided(call(request), record))


class AsyncRouted(RouterSetup):
    async def _arouted(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], Awaitable[_ResponseT]],
    ) -> _ResponseT:
        """Await ``call``, which calls ``chain``, as :meth:`SyncRouted._routed` runs it."""
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else await self._attached().acall(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with _logged(scope, route) as logged:
                return logged.settle(_decided(await call(request), record))

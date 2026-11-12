"""A routed call: when to ask the attached router, what it is shown, and how its answer orders the call.

Experimental. The routers take :class:`SyncRouted` or :class:`AsyncRouted` as
a mixin, and every method that can be routed -- search, extract, answer,
research and browse -- runs through ``_routed`` / ``_arouted``. The router is
asked only when one is attached, the method is in ``route_tools`` (the
caller's; else the router's own ``route_tools`` attribute; else all five), and the
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
from time import monotonic
from typing import Any, TypeVar

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_invoke import ROUTER_URL_POLICY_ATTR
from web_router.routing.attempts import CallRoute, in_route
from web_router.routing.budget import BudgetedCall, budget_scope, recorded
from web_router.routing.candidates import REQUEST_OF, CandidateSet, build_candidates
from web_router.routing.contract import Budget, RouteRequest, ToolName
from web_router.routing.cost_order import cost_route
from web_router.routing.decide import decided
from web_router.routing.effort_call import Only, named, unrouted, with_record
from web_router.routing.invoke import AttachedRouter, RouterLike, RouterOutcome
from web_router.routing.merge_urls import UrlCheck
from web_router.routing.metered import metered
from web_router.routing.pool import limited
from web_router.routing.record import prompt_of
from web_router.routing.route_tools import ALL_TOOLS, checked_route_tools
from web_router.routing.router_owner import RouterOwner
from web_router.routing.url_policy import UrlPolicy, url_check
from web_router.spend import SpendSink
from web_router.spend_record import log_spent
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)

_TOOL_OF: dict[type[NormalizedRequest], ToolName] = {cls: tool for tool, cls in REQUEST_OF.items()}
"""The tool a request is for, read from its type."""


class RouterSetup(RouterOwner):
    """The attached router and the tools it is consulted on, shared by both routers."""

    _router: AttachedRouter | None = None
    _route_tools: frozenset[ToolName] = ALL_TOOLS
    _url_check: UrlCheck | None = None
    _router_url_check: UrlCheck | None = None
    _spend_sink: SpendSink | None = None
    """Where each answered call is logged: set by ``log_spend()``, ``None`` logs nothing."""
    """The check on the URLs a router sets: the caller's ``url_policy``, else the router's ``min_url_policy``."""

    def _attach_router(
        self,
        router: RouterLike | str | None,
        route_tools: Collection[ToolName] | None,
        fail_on_router_error: bool,
        url_policy: UrlPolicy | None = None,
        router_config: Mapping[str, Any] | None = None,
    ) -> None:
        """Attach the router, last in a constructor: a refusal after it must close a router built by name.

        Raises:
            ConfigurationError: for a ``router`` in neither form nor a registered name, ``router_config`` without a
                name, a ``route_tools`` -- the caller's, or the one the router declares -- naming something else,
                or an unknown ``url_policy``.
        """
        self._url_check = url_check(url_policy)
        # Checked once, before a router is built by name, and that result kept: the caller's value is not read again.
        callers = None if route_tools is None else checked_route_tools(route_tools)
        with self._built(router, router_config) as attached:
            self._route_tools = checked_route_tools(None, attached) if callers is None else callers
            self._router = None if attached is None else AttachedRouter(attached, fail_on_error=fail_on_router_error)
            floor = getattr(attached, ROUTER_URL_POLICY_ATTR, None) if url_policy is None else url_policy
            self._router_url_check = url_check(floor)

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

    def _spent(self, response: _ResponseT, tool: ToolName) -> _ResponseT:
        """``response``, written to the spend log first when the router keeps one (``log_spend()``)."""
        log_spent(self._spend_sink, response, tool)
        return response

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
        """The order in force, the request and the record: the router's merged, else the budget's effort and order."""
        if routed is None or outcome is None:
            request, mapped = unrouted(chain, request, budget)
            route, ordered = cost_route(chain, _TOOL_OF[type(request)], request, budget)
            if ordered is not None:
                mapped = ordered if mapped is None else mapped.model_copy(update={"merged": ordered.merged})
            return route, request, mapped
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

        A routed call runs in the order the router chose, and its response records how. Every response
        carries what the call took and cost (:func:`~.metered.metered`).
        """
        started = monotonic()
        request, chosen = named(chain, request, limited(budget, self))  # before the scope, which admits a named one
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request, self) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else self._attached().call(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with _logged(scope, route) as logged:
                answered = logged.settle(with_record(call(request), record or chosen))
        tool = _TOOL_OF[type(request)]
        return self._spent(metered(answered, tool, request, started), tool)


class AsyncRouted(RouterSetup):
    async def _arouted(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], Awaitable[_ResponseT]],
    ) -> _ResponseT:
        """Await ``call``, which calls ``chain``, as :meth:`SyncRouted._routed` runs it."""
        started = monotonic()
        request, chosen = named(chain, request, limited(budget, self))  # before the scope, which admits a named one
        with budget_scope(budget, _TOOL_OF[type(request)], chain, request, self) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else await self._attached().acall(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with _logged(scope, route) as logged:
                answered = logged.settle(with_record(await call(request), record or chosen))
        tool = _TOOL_OF[type(request)]
        return self._spent(metered(answered, tool, request, started), tool)

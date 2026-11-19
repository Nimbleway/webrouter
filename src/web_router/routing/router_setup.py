"""What a router is shown for a routed call, and how its answer orders the call: the attached router's setup.

Experimental. :class:`RouterSetup` holds the attached router, the tools it is
asked about, whether it is shown vendor modes, what the caller prefers and the
URL checks; both routers mix it in through :mod:`web_router.routing.routed`,
which runs every routable call through it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel

from web_router.constants import PROVIDER_AUTO
from web_router.constants_invoke import ROUTER_URL_POLICY_ATTR
from web_router.providers._runtime import Runtime
from web_router.routing.attempts import CallRoute
from web_router.routing.candidates import REQUEST_OF, CandidateSet, build_candidates
from web_router.routing.contract import Budget, Prefer, RouteRequest, ToolName
from web_router.routing.cost_order import cost_route
from web_router.routing.decide import decided
from web_router.routing.effort_call import Only, unrouted
from web_router.routing.invoke import AttachedRouter, RouterLike, RouterOutcome
from web_router.routing.merge_urls import UrlCheck
from web_router.routing.record import prompt_of
from web_router.routing.request_modes import runnable
from web_router.routing.route_tools import ALL_TOOLS, checked_prefer, checked_route_modes, checked_route_tools
from web_router.routing.router_owner import RouterOwner
from web_router.routing.url_policy import UrlPolicy, url_check
from web_router.spend_record import SpendSinkHolder
from web_router.types import NormalizedRequest
from web_router.types_decision import DecisionRecord

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)

TOOL_OF: dict[type[NormalizedRequest], ToolName] = {cls: tool for tool, cls in REQUEST_OF.items()}
"""The tool a request is for, read from its type."""


class RouterSetup(RouterOwner, SpendSinkHolder):
    """The attached router and the tools it is consulted on, shared by both routers."""

    _router: AttachedRouter | None = None
    _route_tools: frozenset[ToolName] = ALL_TOOLS
    _route_modes: bool = False
    _prefer: Prefer | None = None
    """What the caller prefers, shown to the router on every call it is asked about."""
    _url_check: UrlCheck | None = None
    _router_url_check: UrlCheck | None = None
    """The check on the URLs a router sets: the caller's ``url_policy``, else the router's ``min_url_policy``."""
    _runtime: Runtime | None = None
    """The router's cooldowns and ``max_concurrency`` cap, lent to every call it routes; set by its constructor."""

    def _attach_router(
        self,
        router: RouterLike | str | None,
        route_tools: Collection[ToolName] | None,
        fail_on_router_error: bool,
        url_policy: UrlPolicy | None = None,
        router_config: Mapping[str, Any] | None = None,
        route_modes: bool | None = None,
        prefer: Prefer | None = None,
    ) -> None:
        """Attach the router, last in a constructor: a refusal after it must close a router built by name.

        Raises:
            ConfigurationError: for a ``router`` in neither form nor a registered name, ``router_config`` without a
                name, a ``route_tools`` -- the caller's, or the one the router declares -- naming something else,
                a ``route_modes`` that is not a bool, an unknown ``url_policy``, or an unknown ``prefer``.
        """
        self._url_check = url_check(url_policy)
        self._prefer = checked_prefer(prefer)
        # Checked once, before a router is built by name, and that result kept: the caller's value is not read again.
        callers = None if route_tools is None else checked_route_tools(route_tools)
        modes = None if route_modes is None else checked_route_modes(route_modes)
        with self._built(router, router_config) as attached:
            self._route_tools = checked_route_tools(None, attached) if callers is None else callers
            self._route_modes = checked_route_modes(None, attached) if modes is None else modes
            self._router = None if attached is None else AttachedRouter(attached, fail_on_error=fail_on_router_error)
            floor = getattr(attached, ROUTER_URL_POLICY_ATTR, None) if url_policy is None else url_policy
            self._router_url_check = url_check(floor)

    def _route_request(
        self, chain: Any, request: NormalizedRequest, budget: Budget | None
    ) -> tuple[RouteRequest, CandidateSet] | None:
        """What the router is shown for this call, and the candidates; ``None`` when the call is not routed."""
        tool = TOOL_OF.get(type(request))
        if self._router is None or tool is None or tool not in self._route_tools:
            return None
        if getattr(chain, "name", None) != PROVIDER_AUTO:
            return None  # a named backend: nothing to order
        found = build_candidates(Only(tool, chain), {tool: request}, budget=budget, modes=self._route_modes)
        if not runnable(found.candidates):
            return None
        prompt, urls = prompt_of(tool, request)
        req = RouteRequest(
            prompt=prompt,
            candidates=found.candidates,
            deadline_s=self._router.deadline(budget),
            urls=urls,
            budget=budget,
            pinned_tool=tool,
            prefer=self._prefer,
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
        """The order in force, the request and the record: the router's merged, else the budget's effort and order."""
        merged = None if routed is None or outcome is None else self._merged(routed, outcome, request)
        if merged is not None and any(merged[0].order.values()):  # else nothing to run: the budget says why, below
            return merged
        request, mapped = unrouted(chain, request, budget)
        route, ordered = cost_route(chain, TOOL_OF[type(request)], request, budget)
        if ordered is not None:
            mapped = ordered if mapped is None else mapped.model_copy(update={"merged": ordered.merged})
        return route, request, mapped

    def _merged(
        self, routed: tuple[RouteRequest, CandidateSet], outcome: RouterOutcome, request: _RequestT
    ) -> tuple[CallRoute, _RequestT, DecisionRecord]:
        """The order the call runs in, its request with the options a router or the budget set, and the record."""
        req, found = routed
        caller_set = request.specified_parameters() | request.always_sent
        out = decided(
            req, found, outcome, router=self._attached().name, caller_set=caller_set, url_check=self._router_url_check
        )
        request = request.model_copy(update=dict(out.params)) if out.params else request
        return CallRoute(order={out.tool: out.providers}, router_params=frozenset(out.params)), request, out.record

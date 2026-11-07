"""``plan()`` and ``run()`` on the routers: prompt-level routing, kept apart so ``client.py`` stops growing.

Experimental. :meth:`plan` works out, without running anything, which method
a prompt calls, with which options and backends: it finds the ``http(s)``
URLs in the prompt and holds them to ``url_policy``, builds every method's
candidates, asks the attached router, and merges its answer by the merge rule.
:meth:`run` calls the planned method on ``auto`` in the planned order, and
returns that method's own response with the plan's decision record on it,
and what the call tried: its attempts, its hops and the position that ran.
With no router the plan is the static order: ``search``, unless ``tool``
pins another method.

A prompt is sent as each method's main input: search, answer and research
take it as ``query``, browse as ``task`` and starts on its first URL, and
extract reads its URLs. With none, extract has no candidates -- unless a
router is attached, which may supply URLs in its ``params``: then extract is
offered, priced at one page, and the merge rule searches instead when the
router picks it without URLs.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from typing import Any

from web_router._urls import urls_in
from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import AUTO_GETTERS, ONE_PAGE_URL
from web_router.constants_urls import MSG_PLAN_EXTRACT_NO_URL, MSG_PLAN_EXTRACT_REFUSED
from web_router.exceptions import MissingParameterError
from web_router.providers._deadline import call_deadline
from web_router.routing.attempts import CallRoute, call_route
from web_router.routing.budget import budget_scope, recorded
from web_router.routing.candidates import REQUEST_OF, CandidateSet, base_params, build_candidates, default_requests
from web_router.routing.contract import Budget, Plan, RouteRequest, ToolName
from web_router.routing.decide import decided
from web_router.routing.invoke import RouterOutcome
from web_router.routing.record import stamp
from web_router.routing.routed import AsyncRouted, RouterSetup, SyncRouted, in_force_with
from web_router.routing.url_policy import screened
from web_router.types import (
    BrowseResponse,
    ExtractRequest,
    ExtractResponse,
    NormalizedRequest,
    ResearchResponse,
    SearchResponse,
)
from web_router.types_answer import AnswerResponse

RunResponse = SearchResponse | ExtractResponse | AnswerResponse | ResearchResponse | BrowseResponse
"""What :meth:`~web_router.WebRouter.run` returns: the planned method's own response."""


class _Planner(RouterSetup):
    """What both routers share for plan and run: the request a router is shown, the plan, the call it makes."""

    def _plan_request(
        self, prompt: str, budget: Budget | None, tool: ToolName | None
    ) -> tuple[RouteRequest, CandidateSet, list[str], dict[str, str]]:
        """The router's request for ``prompt``, its candidates, the URLs kept, and those the URL policy refused."""
        assert self._url_check is not None  # set by every router's constructor
        kept, refused = screened(list(dict.fromkeys(urls_in(prompt))), self._url_check)  # each URL once
        requests = default_requests(prompt, kept)
        if not kept and self._router is not None:
            # A router may supply the URLs itself; offered at one page, extract is chosen only if it does.
            requests["extract"] = ExtractRequest(urls=[ONE_PAGE_URL])
        if tool is not None:
            requests = {tool: requests[tool]} if tool in requests else {}
        found = build_candidates(self, requests, budget=budget)
        deadline = 0.0 if self._router is None else self._router.deadline(budget)
        req = RouteRequest(
            prompt=prompt,
            candidates=found.candidates,
            deadline_s=deadline,
            urls=tuple(kept),
            budget=budget,
            pinned_tool=tool,
        )
        return req, found, kept, refused

    def _planned(
        self, plan_for: tuple[RouteRequest, CandidateSet, list[str], dict[str, str]], outcome: RouterOutcome | None
    ) -> Plan:
        req, found, kept, refused = plan_for
        name = None if self._router is None else self._router.name
        out = decided(req, found, outcome, router=name, url_check=self._url_check, refused=refused)
        params = {**base_params(out.tool, req.prompt, kept), **out.params}
        if out.tool == "extract" and not params["urls"]:
            # Only a pinned extract gets here: unpinned, the merge rule searches instead. A pin is never moved.
            refusals = "; ".join(f"{host}: {why}" for host, why in refused.items())
            detail = MSG_PLAN_EXTRACT_REFUSED.format(refused=refusals) if refused else MSG_PLAN_EXTRACT_NO_URL
            raise MissingParameterError(PROVIDER_AUTO, ["urls"], detail=detail)
        return Plan(
            prompt=req.prompt, tool=out.tool, params=params, providers={out.tool: out.providers}, decision=out.record
        )

    def _asks(self, req: RouteRequest) -> bool:
        return self._router is not None and bool(req.candidates)

    def _run_scope(self, stack: ExitStack, plan: Plan, budget: Budget | None) -> tuple[Any, NormalizedRequest, Any]:
        """The ``auto`` the plan runs on, its request, and the budget's scope, entered with the plan's order."""
        chain = getattr(self, AUTO_GETTERS[plan.tool])(PROVIDER_AUTO)
        request = REQUEST_OF[plan.tool](**{k: v for k, v in plan.params.items() if v is not None})
        scope = stack.enter_context(recorded(stack.enter_context(budget_scope(budget, plan.tool, chain, request))))
        _ordered(stack, plan.tool, list(plan.providers.get(plan.tool, ())))
        return chain, request, scope


def _ordered(stack: ExitStack, tool: ToolName, providers: Sequence[str]) -> None:
    """Put ``providers`` in force as ``tool``'s order beside the budget's hook; none leaves ``auto``'s own."""
    if providers:
        stack.enter_context(call_route(in_force_with(CallRoute(order={tool: providers}))))


def _settled(response: Any, plan: Plan) -> Any:
    """``response`` with the plan's record; a plan made by hand, whose record has no id, is stamped here."""
    record = plan.decision if plan.decision.decision_id else plan.decision.model_copy(update=stamp(plan.prompt))
    return response.model_copy(update={"decision": record})


class SyncPlan(_Planner, SyncRouted):
    def plan(self, prompt: str, *, budget: Budget | None = None, tool: ToolName | None = None) -> Plan:
        """Work out, without running it, the method, options and backends ``prompt`` would run with (experimental).

        Args:
            prompt: What the caller asks, in words; any ``http(s)`` URL in it is read as one to fetch.
            budget: Caps the plan's candidates and maps research's effort, as on any routed call.
            tool: Pins the method; the router then orders its backends only.

        Raises:
            MissingParameterError: for ``tool="extract"`` when the prompt names no URL to fetch, or ``url_policy``
                refused every one it names, and no router supplied one: a plan ``run`` could not execute.
        """
        parts = self._plan_request(prompt, budget, tool)
        req = parts[0]
        outcome = self._router.call(req) if self._router is not None and self._asks(req) else None
        return self._planned(parts, outcome)

    def run(self, plan: Plan | str, *, budget: Budget | None = None, tool: ToolName | None = None) -> RunResponse:
        """Run ``plan``, or plan ``plan`` the prompt and run it, returning the method's own response (experimental).

        The response's ``decision`` is the plan's record, with what the budget spent. ``budget`` holds the
        run; for a prompt it holds the planning too. ``tool`` pins the method of a prompt's plan.
        """
        with ExitStack() as stack:
            if isinstance(plan, str):
                stack.enter_context(call_deadline(None if budget is None else budget.time_limit_s))
                plan = self.plan(plan, budget=budget, tool=tool)
            chain, request, scope = self._run_scope(stack, plan, budget)
            return scope.settle(_settled(getattr(chain, plan.tool)(request), plan))  # type: ignore[no-any-return]


class AsyncPlan(_Planner, AsyncRouted):
    async def plan(self, prompt: str, *, budget: Budget | None = None, tool: ToolName | None = None) -> Plan:
        """As :meth:`SyncPlan.plan`, asking the router without blocking the event loop."""
        parts = self._plan_request(prompt, budget, tool)
        req = parts[0]
        outcome = await self._router.acall(req) if self._router is not None and self._asks(req) else None
        return self._planned(parts, outcome)

    async def run(self, plan: Plan | str, *, budget: Budget | None = None, tool: ToolName | None = None) -> RunResponse:
        """As :meth:`SyncPlan.run`."""
        with ExitStack() as stack:
            if isinstance(plan, str):
                stack.enter_context(call_deadline(None if budget is None else budget.time_limit_s))
                plan = await self.plan(plan, budget=budget, tool=tool)
            chain, request, scope = self._run_scope(stack, plan, budget)
            return scope.settle(_settled(await getattr(chain, f"a{plan.tool}")(request), plan))  # type: ignore[no-any-return]


__all__ = ["AsyncPlan", "RunResponse", "SyncPlan"]

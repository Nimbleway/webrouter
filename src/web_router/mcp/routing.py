"""The ``web_run`` tool and the routing tools' table: plan a prompt with the operator's router, and run it.

Experimental. The router and the ceiling come from
:class:`~web_router.mcp.routing_config.McpRouting`, read once at startup; the
budget tools are :mod:`web_router.mcp.budget`'s.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from web_router.constants import (
    MCP_MSG_BROWSE_PENDING,
    MCP_MSG_RESEARCH_PENDING,
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_SEARCH,
    PROVIDER_AUTO,
)
from web_router.constants_candidates import AUTO_GETTERS
from web_router.constants_mcp import (
    MCP_ROUTER_OPERATOR,
    MCP_TOOL_BUDGET_START,
    MCP_TOOL_BUDGET_STATUS,
    MCP_TOOL_WEB_RUN,
)
from web_router.exceptions import BrowseTimeoutError, ResearchTimeoutError
from web_router.mcp.budget import budget_start, budget_status, positive_limit, wait_left
from web_router.mcp.shaping import (
    max_response_chars,
    redact,
    shape_answer,
    shape_browse,
    shape_extract,
    shape_pending,
    shape_research,
    shape_search,
)
from web_router.providers._deadline import call_deadline
from web_router.providers.base import BrowseJob, ResearchJob
from web_router.routing.contract import Budget, Plan
from web_router.types import (
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    ExtractResponse,
    NormalizedRequest,
    ResearchProgress,
    ResearchRequest,
    ResearchResponse,
    SearchResponse,
)
from web_router.types_answer import AnswerResponse
from web_router.types_decision import DecisionRecord

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools

_SHAPERS: dict[type, tuple[str, Callable[..., dict[str, Any]]]] = {
    SearchResponse: (MCP_TOOL_SEARCH, shape_search),
    ExtractResponse: (MCP_TOOL_EXTRACT, shape_extract),
    AnswerResponse: (MCP_TOOL_ANSWER, shape_answer),
    ResearchResponse: (MCP_TOOL_RESEARCH, shape_research),
    BrowseResponse: (MCP_TOOL_BROWSE, shape_browse),
}
"""Each response ``run`` can return: the method it is from, and how that method's own tool shapes it."""

_CAPS = ("max_cost_usd", "time_limit_s")
"""``web_run``'s own caps on one call, each held to the same rule as ``budget_start``'s limits."""

_JOBS: dict[str, tuple[type[ResearchJob] | type[BrowseJob], type[NormalizedRequest], type[BaseModel], str]] = {
    MCP_TOOL_RESEARCH: (ResearchJob, ResearchRequest, ResearchProgress, MCP_MSG_RESEARCH_PENDING),
    MCP_TOOL_BROWSE: (BrowseJob, BrowseRequest, BrowseProgress, MCP_MSG_BROWSE_PENDING),
}
"""The methods that run as jobs: ``web_run`` waits for them as their own tools do, then hands back a handle."""


def _shown(tools: WebRouterTools, record: DecisionRecord | None) -> dict[str, Any] | None:
    """``record`` as the client gets it: an operator's router named ``operator``, as capabilities name it."""
    if record is None:
        return None
    shown = record.model_dump(exclude_none=True)
    if tools.routing.router_name == MCP_ROUTER_OPERATOR and "router" in shown:
        shown["router"] = MCP_ROUTER_OPERATOR  # its class or function name is the operator's code, not the client's
    return shown


def _plan_payload(tools: WebRouterTools, plan: Plan) -> dict[str, Any]:
    return {
        "tool": plan.tool,
        "params": dict(plan.params),
        "providers": {tool: list(order) for tool, order in plan.providers.items()},
        "decision": _shown(tools, plan.decision),
    }


async def web_run(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Plan ``prompt`` with the attached router and run it, or with ``dry_run`` only plan it."""
    kwargs = tools.arguments(MCP_TOOL_WEB_RUN, arguments)
    caps = {name: positive_limit(kwargs, name, MCP_TOOL_WEB_RUN) for name in _CAPS if name in kwargs}
    budget = Budget(max_cost_usd=caps.get("max_cost_usd"), time_limit_s=caps.get("time_limit_s")) if caps else None
    prompt = str(kwargs["prompt"])
    with call_deadline(caps.get("time_limit_s")):  # the call's own time limit covers its planning too
        plan = await tools.router.plan(prompt, budget=budget)
        if kwargs.get("dry_run"):
            return {"dry_run": True, **_plan_payload(tools, plan)}
        return await _ran(tools, plan, budget)


async def _ran(tools: WebRouterTools, plan: Plan, budget: Budget | None) -> dict[str, Any]:
    """``plan`` run, shaped as its method's own tool shapes it; a job still going after its wait, as a handle."""
    wait = {MCP_TOOL_RESEARCH: tools.research_wait, MCP_TOOL_BROWSE: tools.browse_wait}.get(plan.tool)
    waited = None if wait is None else wait_left(wait)
    try:
        # A job is waited for as its own tool waits, then handed back by the id its own timeout carries.
        with call_deadline(waited):
            response = await tools.router.run(plan, budget=budget)
    except (ResearchTimeoutError, BrowseTimeoutError) as exc:
        return _pending(tools, plan, exc.job_id, waited or 0.0)
    tool, shape = _SHAPERS[type(response)]
    # A browse payload can carry the session the agent ran in, so fetch_raw hands back its redacted copy.
    raw = redact(response.raw)[0] if tool == MCP_TOOL_BROWSE else response.raw
    handle = tools.store.add(MCP_TOOL_WEB_RUN, raw=raw).handle
    return {
        "tool": tool,
        **shape(response, handle, budget=max_response_chars()),
        "decision": _shown(tools, response.decision),
    }


def _pending(tools: WebRouterTools, plan: Plan, job_id: str, wait: float) -> dict[str, Any]:
    """The job ``plan`` started on its ``auto``, still running after ``wait``: a handle its status tool polls."""
    job_cls, request_cls, progress_cls, note = _JOBS[plan.tool]
    chain = getattr(tools.router, AUTO_GETTERS[plan.tool])(PROVIDER_AUTO)  # the instance that holds the run
    request = request_cls(**{name: value for name, value in plan.params.items() if value is not None})
    job = job_cls(chain, request, progress_cls(job_id=job_id, state="running"))  # type: ignore[arg-type]
    handle = tools.store.add(plan.tool, job=job).handle
    pending = shape_pending(job, handle, wait=wait, note=note)
    return {"tool": plan.tool, **pending, "decision": _shown(tools, plan.decision)}


ROUTING_TOOLS: dict[str, Callable[[WebRouterTools, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    MCP_TOOL_WEB_RUN: web_run,
    MCP_TOOL_BUDGET_START: budget_start,
    MCP_TOOL_BUDGET_STATUS: budget_status,
}
"""The three routing tools, each a handler taking the session's tools, as :class:`WebRouterTools` registers them."""

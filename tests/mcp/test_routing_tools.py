"""The MCP routing tools: ``web_run``, ``budget_start`` / ``budget_status``, the operator's ceiling, capabilities.

Every method's ``auto`` runs over the fake members ``first``, ``second`` and
``third`` from :mod:`tests.routing_helpers`, all answering, so ``first`` is the
one that runs; prices are the test's own, by member name.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.mcp.conftest import URL, Table, ok_on, outcome, routed
from tests.routing_helpers import MEMBERS
from web_router import RouteDecision, RouteRequest
from web_router.constants_budget import SKIP_NO_TIME, SKIP_PRICE_UNKNOWN
from web_router.constants_mcp import (
    ENV_MCP_ROUTER,
    MCP_ROUTER_OFF,
    MCP_TOOL_BUDGET_START,
    MCP_TOOL_BUDGET_STATUS,
    MCP_TOOL_WEB_RUN,
)
from web_router.constants_routing import RULES_ROUTER_NAME
from web_router.routing import Budget
from web_router.routing.pool import current_pool

ANY = object()
"""Any router: the connection's budget has no owner, so it holds every router's calls."""


# -- web_run -------------------------------------------------------------------


@pytest.mark.parametrize(("prompt", "tool"), [(f"what does {URL} say?", "extract"), ("latest on solar", "search")])
async def test_a_dry_run_plans_with_the_rules_router_and_runs_nothing(
    members: list[str], prompt: str, tool: str
) -> None:
    plan = await ok_on(routed(), MCP_TOOL_WEB_RUN, prompt=prompt, dry_run=True)
    assert (plan["dry_run"], plan["tool"]) == (True, tool)
    assert plan["providers"][tool] == list(MEMBERS)
    assert plan["decision"]["router"] == RULES_ROUTER_NAME
    assert members == [], "a dry run calls no backend"


async def test_web_run_runs_the_plan_shaped_like_the_method_with_a_handle_and_its_record(members: list[str]) -> None:
    tools = routed()
    shaped = await ok_on(tools, MCP_TOOL_WEB_RUN, prompt=f"read {URL}")
    assert shaped["tool"] == "extract" and members == ["first"]
    assert shaped["decision"]["router"] == RULES_ROUTER_NAME and shaped["decision"]["ran"] == 0
    raw = await ok_on(tools, "fetch_raw", handle=shaped["handle"])
    assert raw["tool"] == MCP_TOOL_WEB_RUN


async def test_web_run_with_routing_off_plans_the_static_order(members: list[str]) -> None:
    plan = await ok_on(routed(**{ENV_MCP_ROUTER: MCP_ROUTER_OFF}), MCP_TOOL_WEB_RUN, prompt=f"read {URL}", dry_run=True)
    assert plan["tool"] == "search" and "router" not in plan["decision"]


async def test_web_run_holds_its_own_cap(members: list[str], priced: Table) -> None:
    priced.update(first=0.04, second=0.01, third=0.01)
    shaped = await ok_on(routed(), MCP_TOOL_WEB_RUN, prompt="q", max_cost_usd=0.02)
    assert members == ["second"], "first does not fit the call's own cap"
    assert shaped["decision"]["estimated_cost_usd"] == 0.01


def route_to_extract(req: RouteRequest) -> RouteDecision:
    """An operator's router, imported by name: always extract."""
    return RouteDecision(tools=["extract"], reason="operator")


async def test_the_operator_attaches_a_router_by_module_and_attribute(members: list[str]) -> None:
    spec = f"{__name__}:route_to_extract"
    tools = routed(**{ENV_MCP_ROUTER: spec})
    plan = await ok_on(tools, MCP_TOOL_WEB_RUN, prompt=f"read {URL}", dry_run=True)
    assert plan["decision"]["reason"] == "operator"
    assert tools.capabilities()["routing"]["router"] == spec


# -- no router and no budget ---------------------------------------------------


async def test_with_no_ceiling_and_no_budget_start_no_budget_is_bound(members: list[str]) -> None:
    tools = routed()
    seen: list[object] = []
    tools._handlers["probe"] = lambda arguments: _record(seen)
    await tools.call("probe", {})
    assert seen == [None] and tools.budget.status() is None
    assert tools.capabilities()["routing"]["budget"] is None


async def _record(seen: list[object]) -> dict[str, Any]:
    seen.append(current_pool())
    return {}


ASKED: list[RouteRequest] = []
SPYING = f"{__name__}:spying"


def spying(req: RouteRequest) -> RouteDecision:
    """An operator's router that notes every request it is shown."""
    ASKED.append(req)
    return RouteDecision()


async def test_the_direct_tools_never_ask_the_attached_router(members: list[str]) -> None:
    ASKED.clear()
    tools = routed(**{ENV_MCP_ROUTER: f"{__name__}:spying"})
    await ok_on(tools, "search", query="q")
    await ok_on(tools, "extract", urls=[URL])
    assert ASKED == [] and members == ["first", "first"]
    await ok_on(tools, MCP_TOOL_WEB_RUN, prompt="q")
    assert len(ASKED) == 1, "web_run does"


# -- the ceiling ---------------------------------------------------------------


async def test_the_ceiling_refuses_a_call_that_does_not_fit_what_is_left(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    tools = routed(Budget(max_cost_usd=0.05))
    await ok_on(tools, "search", query="q", provider="first")
    failed, message = await outcome(tools, "search", query="q", provider="first")
    assert failed and "Nothing fits the budget" in message
    status = await ok_on(tools, MCP_TOOL_BUDGET_STATUS)
    assert status["budget"]["spent_usd"] == 0.04 and status["ceiling"]["max_cost_usd"] == 0.05


async def test_a_direct_research_call_past_the_ceiling_is_refused(members: list[str], priced: Table) -> None:
    priced.update(first=1.0)
    failed, message = await outcome(routed(Budget(max_cost_usd=0.5)), "research", query="q", provider="first")
    assert failed and "research:first" in message and "est. $1.00" in message
    assert members == []


async def test_under_a_ceiling_an_unknown_price_research_backend_is_refused_naming_it(
    members: list[str], priced: Table
) -> None:
    failed, message = await outcome(routed(Budget(max_cost_usd=5.0)), "research", query="q", provider="first")
    assert failed and SKIP_PRICE_UNKNOWN in message
    assert members == []


async def test_a_template_run_spends_from_the_ceiling(fake_template_provider: Any, priced: Table) -> None:
    priced.update(fake=0.04)
    tools = routed(Budget(max_cost_usd=0.05))
    await ok_on(tools, "run_template", template="books_pdp", provider="fake", inputs={"url": URL})
    failed, message = await outcome(tools, "run_template", template="books_pdp", provider="fake", inputs={"url": URL})
    assert failed and "run_template:fake" in message


# -- budget_start and budget_status --------------------------------------------


async def test_budget_start_opens_a_task_the_calls_spend_from(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    tools = routed()
    started = await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=0.1, time_limit_s=60)
    assert started["budget"]["max_cost_usd"] == 0.1 and started["budget"]["spent_usd"] == 0.0
    await ok_on(tools, "search", query="q", provider="first")
    status = (await ok_on(tools, MCP_TOOL_BUDGET_STATUS))["budget"]
    assert (status["spent_usd"], status["reserved_usd"], status["remaining_usd"]) == (0.04, 0.0, 0.06)
    assert 0 < status["time_left_s"] <= 60 and status["elapsed_s"] >= 0


async def test_budget_start_is_capped_by_what_the_ceiling_has_left(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    tools = routed(Budget(max_cost_usd=0.1))
    await ok_on(tools, "search", query="q", provider="first")
    started = (await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=60))["budget"]
    assert (started["max_cost_usd"], started["remaining_usd"]) == (0.06, 0.06), "the most this task can spend"
    await ok_on(tools, "search", query="q", provider="first")
    failed, message = await outcome(tools, "search", query="q", provider="first")
    assert failed and "$0.02 left" in message, "the task allows it; the ceiling does not"


async def test_the_task_clock_runs_from_budget_start_and_a_new_task_restarts_it(
    members: list[str], priced: Table
) -> None:
    priced.update(first=0.01)
    tools = routed()
    await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=0.05)
    time.sleep(0.1)
    failed, message = await outcome(tools, "search", query="q", provider="first")
    assert failed and SKIP_NO_TIME in message
    await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=60)
    await ok_on(tools, "search", query="q", provider="first")
    assert members == ["first"]


async def test_budget_status_reports_the_limits_in_force_cut_to_the_ceiling() -> None:
    tools = routed(Budget(max_cost_usd=1.0, time_limit_s=60))
    started = (await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=5.0, time_limit_s=1000))["budget"]
    assert started["max_cost_usd"] == 1.0 and started["time_limit_s"] <= 60
    shorter = (await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=0.5, time_limit_s=10))["budget"]
    assert (shorter["max_cost_usd"], shorter["time_limit_s"]) == (0.5, 10.0)

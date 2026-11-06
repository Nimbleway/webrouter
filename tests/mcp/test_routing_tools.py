"""The MCP routing tools: ``web_run``, ``budget_start`` / ``budget_status``, the operator's ceiling, capabilities.

Every method's ``auto`` runs over the fake members ``first``, ``second`` and
``third`` from :mod:`tests.test_auto_seam`, all answering, so ``first`` is the
one that runs; prices are the test's own, by member name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_auto_seam import MEMBERS, TOOLS, _member
from web_router import RouteDecision, RouteRequest, prices
from web_router.constants_budget import SKIP_NO_TIME, SKIP_PRICE_UNKNOWN
from web_router.constants_mcp import (
    ENV_MCP_MAX_COST_USD,
    ENV_MCP_ROUTER,
    ENV_MCP_TIME_LIMIT_S,
    MCP_ROUTER_OFF,
    MCP_TOOL_BUDGET_START,
    MCP_TOOL_BUDGET_STATUS,
    MCP_TOOL_WEB_RUN,
)
from web_router.constants_routing import RULES_ROUTER_NAME
from web_router.exceptions import BudgetExceededError, ConfigurationError
from web_router.mcp.budget import ConnectionBudget, charged
from web_router.mcp.routing import McpRouting
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools
from web_router.routing import Budget
from web_router.routing.pool import current_pool
from web_router.types import BrowserRequest

URL = "https://example.com/report"
CONFIG = {"auto": {"chain": list(MEMBERS)}}

Table = dict[str, float | None]


@pytest.fixture
def members() -> Iterator[list[str]]:
    """``first``, ``second`` and ``third`` for every method, all answering; who was called."""
    log: list[str] = []
    for tool in TOOLS:
        for name in MEMBERS:
            tool.register(name, _member(tool, name, log, fails=False))
    yield log
    for tool in TOOLS:
        for name in MEMBERS:
            tool.unregister(name)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> Table:
    """The price the budget reads for each member by name, whatever the method; unlisted is unknown."""
    table: Table = {}

    def price(provider: str, method: str, mode: str | None = None) -> SimpleNamespace | None:
        cost = table.get(provider)
        return None if cost is None else SimpleNamespace(estimate_usd=cost, estimate_is_floor=False, mode=mode)

    monkeypatch.setattr(prices, "price", price)
    monkeypatch.setattr(prices, "estimate_latency", lambda *args, **kwargs: None)
    return table


def _tools(ceiling: Budget | None = None, **environ: str) -> WebRouterTools:
    routing = McpRouting.from_environ(environ)
    if ceiling is not None:
        routing = McpRouting(router=routing.router, router_name=routing.router_name, ceiling=ceiling)
    return WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, research_wait=0.0)


async def _call(tools: WebRouterTools, name: str, **arguments: Any) -> tuple[bool, Any]:
    result = await call_tool(tools, name, arguments)
    text = result.content[0].text
    return bool(result.is_error), text if result.is_error else json.loads(text)


async def _ok(tools: WebRouterTools, name: str, **arguments: Any) -> dict[str, Any]:
    failed, payload = await _call(tools, name, **arguments)
    assert not failed, payload
    return dict(payload)


# -- web_run -------------------------------------------------------------------


@pytest.mark.parametrize(("prompt", "tool"), [(f"what does {URL} say?", "extract"), ("latest on solar", "search")])
async def test_a_dry_run_plans_with_the_rules_router_and_runs_nothing(
    members: list[str], prompt: str, tool: str
) -> None:
    plan = await _ok(_tools(), MCP_TOOL_WEB_RUN, prompt=prompt, dry_run=True)
    assert (plan["dry_run"], plan["tool"]) == (True, tool)
    assert plan["providers"][tool] == list(MEMBERS)
    assert plan["decision"]["router"] == RULES_ROUTER_NAME
    assert members == [], "a dry run calls no backend"


async def test_web_run_runs_the_plan_shaped_like_the_method_with_a_handle_and_its_record(members: list[str]) -> None:
    tools = _tools()
    shaped = await _ok(tools, MCP_TOOL_WEB_RUN, prompt=f"read {URL}")
    assert shaped["tool"] == "extract" and members == ["first"]
    assert shaped["decision"]["router"] == RULES_ROUTER_NAME and shaped["decision"]["ran"] == 0
    raw = await _ok(tools, "fetch_raw", handle=shaped["handle"])
    assert raw["tool"] == MCP_TOOL_WEB_RUN


async def test_web_run_with_routing_off_plans_the_static_order(members: list[str]) -> None:
    plan = await _ok(_tools(**{ENV_MCP_ROUTER: MCP_ROUTER_OFF}), MCP_TOOL_WEB_RUN, prompt=f"read {URL}", dry_run=True)
    assert plan["tool"] == "search" and "router" not in plan["decision"]


async def test_web_run_holds_its_own_cap(members: list[str], priced: Table) -> None:
    priced.update(first=0.04, second=0.01, third=0.01)
    shaped = await _ok(_tools(), MCP_TOOL_WEB_RUN, prompt="q", max_cost_usd=0.02)
    assert members == ["second"], "first does not fit the call's own cap"
    assert shaped["decision"]["estimated_cost_usd"] == 0.01


def route_to_extract(req: RouteRequest) -> RouteDecision:
    """An operator's router, imported by name: always extract."""
    return RouteDecision(tools=["extract"], reason="operator")


async def test_the_operator_attaches_a_router_by_module_and_attribute(members: list[str]) -> None:
    spec = f"{__name__}:route_to_extract"
    tools = _tools(**{ENV_MCP_ROUTER: spec})
    plan = await _ok(tools, MCP_TOOL_WEB_RUN, prompt=f"read {URL}", dry_run=True)
    assert plan["decision"]["reason"] == "operator"
    assert tools.capabilities()["routing"]["router"] == spec


@pytest.mark.parametrize(
    "environ",
    [
        {ENV_MCP_ROUTER: "no_colon"},
        {ENV_MCP_ROUTER: "not_a_module_here:router"},
        {ENV_MCP_ROUTER: f"{__name__}:missing"},
        {ENV_MCP_MAX_COST_USD: "lots"},
        {ENV_MCP_TIME_LIMIT_S: "-5"},
    ],
)
def test_a_value_the_server_cannot_read_stops_it_at_startup(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        McpRouting.from_environ(environ)


# -- no router and no budget ---------------------------------------------------


async def test_with_no_ceiling_and_no_budget_start_no_budget_is_bound(members: list[str]) -> None:
    tools = _tools()
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
    tools = _tools(**{ENV_MCP_ROUTER: f"{__name__}:spying"})
    await _ok(tools, "search", query="q")
    await _ok(tools, "extract", urls=[URL])
    assert ASKED == [] and members == ["first", "first"]
    await _ok(tools, MCP_TOOL_WEB_RUN, prompt="q")
    assert len(ASKED) == 1, "web_run does"


# -- the ceiling ---------------------------------------------------------------


async def test_the_ceiling_refuses_a_call_that_does_not_fit_what_is_left(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    tools = _tools(Budget(max_cost_usd=0.05))
    await _ok(tools, "search", query="q", provider="first")
    failed, message = await _call(tools, "search", query="q", provider="first")
    assert failed and "Nothing fits the budget" in message
    status = await _ok(tools, MCP_TOOL_BUDGET_STATUS)
    assert status["budget"]["spent_usd"] == 0.04 and status["ceiling"]["max_cost_usd"] == 0.05


async def test_a_direct_research_call_past_the_ceiling_is_refused(members: list[str], priced: Table) -> None:
    priced.update(first=1.0)
    failed, message = await _call(_tools(Budget(max_cost_usd=0.5)), "research", query="q", provider="first")
    assert failed and "research:first" in message and "est. $1.00" in message
    assert members == []


async def test_under_a_ceiling_an_unknown_price_research_backend_is_refused_naming_it(
    members: list[str], priced: Table
) -> None:
    failed, message = await _call(_tools(Budget(max_cost_usd=5.0)), "research", query="q", provider="first")
    assert failed and SKIP_PRICE_UNKNOWN in message
    assert members == []


async def test_a_template_run_spends_from_the_ceiling(fake_template_provider: Any, priced: Table) -> None:
    priced.update(fake=0.04)
    tools = _tools(Budget(max_cost_usd=0.05))
    await _ok(tools, "run_template", template="books_pdp", provider="fake", inputs={"url": URL})
    failed, message = await _call(tools, "run_template", template="books_pdp", provider="fake", inputs={"url": URL})
    assert failed and "run_template:fake" in message


# -- budget_start and budget_status --------------------------------------------


async def test_budget_start_opens_a_task_the_calls_spend_from(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    tools = _tools()
    started = await _ok(tools, MCP_TOOL_BUDGET_START, max_cost_usd=0.1, time_limit_s=60)
    assert started["budget"]["max_cost_usd"] == 0.1 and started["budget"]["spent_usd"] == 0.0
    await _ok(tools, "search", query="q", provider="first")
    status = (await _ok(tools, MCP_TOOL_BUDGET_STATUS))["budget"]
    assert (status["spent_usd"], status["reserved_usd"], status["remaining_usd"]) == (0.04, 0.0, 0.06)
    assert 0 < status["time_left_s"] <= 60 and status["elapsed_s"] >= 0


async def test_budget_start_is_capped_by_what_the_ceiling_has_left(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    tools = _tools(Budget(max_cost_usd=0.1))
    await _ok(tools, "search", query="q", provider="first")
    started = (await _ok(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=60))["budget"]
    assert started["max_cost_usd"] == 0.1 and started["remaining_usd"] == pytest.approx(0.06)
    await _ok(tools, "search", query="q", provider="first")
    failed, message = await _call(tools, "search", query="q", provider="first")
    assert failed and "left in the shared budget" in message, "the task allows it; the ceiling does not"


async def test_the_task_clock_runs_from_budget_start_and_a_new_task_restarts_it(
    members: list[str], priced: Table
) -> None:
    priced.update(first=0.01)
    tools = _tools()
    await _ok(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=0.05)
    time.sleep(0.1)
    failed, message = await _call(tools, "search", query="q", provider="first")
    assert failed and SKIP_NO_TIME in message
    await _ok(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=60)
    await _ok(tools, "search", query="q", provider="first")
    assert members == ["first"]


@pytest.mark.parametrize("bad", [0, -1, "1", True])
async def test_budget_start_takes_only_positive_numbers(bad: object) -> None:
    failed, message = await _call(_tools(), MCP_TOOL_BUDGET_START, max_cost_usd=bad, time_limit_s=10)
    assert failed and "max_cost_usd" in message


# -- the capabilities resource and the environment -----------------------------


def test_the_capabilities_routing_section() -> None:
    routing = _tools(Budget(max_cost_usd=2.0, time_limit_s=600)).capabilities()["routing"]
    assert {key: routing[key] for key in ("router", "prompts", "url_policy")} == {
        "router": RULES_ROUTER_NAME,
        "prompts": "in_process",
        "url_policy": "public_only",
    }
    assert routing["ceiling"] == {"max_cost_usd": 2.0, "time_limit_s": 600.0}
    assert routing["budget"]["remaining_usd"] == 2.0


def test_the_ceiling_is_read_from_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_MAX_COST_USD, "1.5")
    monkeypatch.setenv(ENV_MCP_TIME_LIMIT_S, "300")
    assert WebRouterTools().routing.ceiling == Budget(max_cost_usd=1.5, time_limit_s=300.0)


async def test_load_env_never_sets_or_changes_the_ceiling_or_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    monkeypatch.setenv(ENV_MCP_MAX_COST_USD, "1.5")
    tools = WebRouterTools()
    env_file = tmp_path / "keys.env"
    env_file.write_text(f"{ENV_MCP_MAX_COST_USD}=999\n{ENV_MCP_ROUTER}={MCP_ROUTER_OFF}\nTAVILY_API_KEY=k\n")
    await _call(tools, "load_env", path=str(env_file))
    routing = tools.capabilities()["routing"]
    assert routing["ceiling"]["max_cost_usd"] == 1.5 and routing["router"] == RULES_ROUTER_NAME
    assert tools.router._router is not None, "the rebuilt router keeps the router attached"


def test_a_browser_is_charged_per_minute_of_its_lifetime_on_the_dearest_backend_it_may_open_on(
    priced: Table,
) -> None:
    priced.update(cheap=0.001, dear=0.01)
    budget = ConnectionBudget(Budget(max_cost_usd=0.05))
    with budget.bound(), charged("open_browser", ["cheap", "dear"], BrowserRequest(timeout=120)):
        assert budget.status()["reserved_usd"] == pytest.approx(0.02)
    unknown = charged("open_browser", ["cheap", "dear"], BrowserRequest(timeout=None))
    with budget.bound(), pytest.raises(BudgetExceededError) as refused, unknown:
        pass
    assert refused.value.skipped == {"open_browser:cheap": SKIP_PRICE_UNKNOWN}, "no lifetime, no price"
    assert budget.status()["spent_usd"] == pytest.approx(0.02)


def test_a_free_browser_needs_no_lifetime(priced: Table) -> None:
    priced.update(local=0.0)
    budget = ConnectionBudget(Budget(max_cost_usd=0.05))
    with budget.bound(), charged("open_browser", ["local"], BrowserRequest()):
        pass
    assert budget.status()["spent_usd"] == 0.0


@pytest.mark.parametrize(
    ("environ", "prompts"),
    [
        ({}, "in_process"),
        ({ENV_MCP_ROUTER: MCP_ROUTER_OFF}, "in_process"),
        ({ENV_MCP_ROUTER: SPYING}, "operator_router"),
    ],
)
def test_prompts_are_reported_in_process_only_for_the_built_in_router_or_none(
    environ: dict[str, str], prompts: str
) -> None:
    routing = _tools(**environ).capabilities()["routing"]
    assert routing["prompts"] == prompts
    assert ("prompts_note" in routing) == (prompts == "operator_router"), "an operator's router may send them elsewhere"


async def test_budget_status_reports_the_limits_in_force_cut_to_the_ceiling() -> None:
    tools = _tools(Budget(max_cost_usd=1.0, time_limit_s=60))
    started = (await _ok(tools, MCP_TOOL_BUDGET_START, max_cost_usd=5.0, time_limit_s=1000))["budget"]
    assert started["max_cost_usd"] == 1.0 and started["time_limit_s"] <= 60
    shorter = (await _ok(tools, MCP_TOOL_BUDGET_START, max_cost_usd=0.5, time_limit_s=10))["budget"]
    assert (shorter["max_cost_usd"], shorter["time_limit_s"]) == (0.5, 10.0)

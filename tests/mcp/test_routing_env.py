"""The operator's side of MCP routing: what startup reads, the capabilities resource, and a browser's charge.

As in :mod:`tests.mcp.test_routing_tools`, over the fake members with the test's own prices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.conftest import Table, ok_on, outcome, routed
from web_router import RouteDecision, RouteRequest
from web_router.constants_budget import SKIP_PRICE_UNKNOWN
from web_router.constants_mcp import (
    ENV_MCP_MAX_COST_USD,
    ENV_MCP_ROUTER,
    ENV_MCP_TIME_LIMIT_S,
    MCP_ROUTER_OFF,
    MCP_ROUTER_OPERATOR,
    MCP_TOOL_WEB_RUN,
)
from web_router.constants_routing import RULES_ROUTER_NAME
from web_router.exceptions import ConfigurationError
from web_router.exceptions_budget import BudgetExceededError
from web_router.mcp.budget import ConnectionBudget, charged
from web_router.mcp.routing_config import McpRouting
from web_router.mcp.tools import WebRouterTools
from web_router.routing import Budget
from web_router.types import BrowserRequest

ANY = object()
"""Any router: the connection's budget has no owner, so it holds every router's calls."""


def operators(req: RouteRequest) -> RouteDecision:
    """An operator's router, imported by name."""
    return RouteDecision()


# -- what startup reads -------------------------------------------------------------


@pytest.mark.parametrize(
    "environ",
    [
        {ENV_MCP_MAX_COST_USD: ""},
        {ENV_MCP_TIME_LIMIT_S: "   "},
        {ENV_MCP_MAX_COST_USD: "inf"},
        {ENV_MCP_MAX_COST_USD: "lots"},
        {ENV_MCP_TIME_LIMIT_S: "-5"},
        {ENV_MCP_MAX_COST_USD: "0"},
        {ENV_MCP_TIME_LIMIT_S: "0.0"},
        {ENV_MCP_ROUTER: ""},
        {ENV_MCP_ROUTER: "   "},
        {ENV_MCP_ROUTER: "no_colon"},
        {ENV_MCP_ROUTER: "not_a_module_here:router"},
        {ENV_MCP_ROUTER: f"{__name__}:missing"},
    ],
)
def test_a_value_the_server_cannot_read_stops_it_at_startup_rather_than_running_uncapped(
    environ: dict[str, str],
) -> None:
    with pytest.raises(ConfigurationError):
        McpRouting.from_environ(environ)


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
    await outcome(tools, "load_env", path=str(env_file))
    routing = tools.capabilities()["routing"]
    assert routing["ceiling"]["max_cost_usd"] == 1.5 and routing["router"] == RULES_ROUTER_NAME
    assert tools.router._router is not None, "the rebuilt router keeps the router attached"


# -- the capabilities resource ------------------------------------------------------


def test_the_capabilities_routing_section() -> None:
    routing = routed(Budget(max_cost_usd=2.0, time_limit_s=600)).capabilities()["routing"]
    assert {key: routing[key] for key in ("router", "router_runs", "url_policy")} == {
        "router": RULES_ROUTER_NAME,
        "router_runs": "in_process",
        "url_policy": "public_only",
    }
    assert routing["ceiling"] == {"max_cost_usd": 2.0, "time_limit_s": 600.0}
    assert "web_run's plans only" in routing["url_policy_note"], "the direct tools take the URLs they are given"
    assert routing["budget"]["remaining_usd"] == 2.0


@pytest.mark.parametrize(
    ("environ", "runs"),
    [
        ({}, "in_process"),
        ({ENV_MCP_ROUTER: MCP_ROUTER_OFF}, "in_process"),
        ({ENV_MCP_ROUTER: f"{__name__}:operators"}, "operator"),
    ],
)
def test_the_router_is_reported_in_process_only_for_the_built_in_router_or_none(
    environ: dict[str, str], runs: str
) -> None:
    routing = routed(**environ).capabilities()["routing"]
    assert routing["router_runs"] == runs and "backend" in routing["prompt_note"], "a backend always gets the prompt"
    assert ("router_note" in routing) == (runs == "operator"), "an operator's router may send the prompt elsewhere"


async def test_an_operators_router_is_named_operator_in_every_record_the_client_gets(members: list[str]) -> None:
    tools = routed(**{ENV_MCP_ROUTER: f"{__name__}:operators"})
    plan = await tools.router.plan("latest on solar")
    assert plan.decision.router == "operators", "the library's own record keeps the router's name"
    for dry_run in (True, False):
        payload = await ok_on(tools, MCP_TOOL_WEB_RUN, prompt="latest on solar", dry_run=dry_run)
        assert payload["decision"]["router"] == MCP_ROUTER_OPERATOR


# -- what a browser is charged -------------------------------------------------------


def test_a_browser_is_charged_per_minute_of_its_lifetime_on_the_dearest_backend_it_may_open_on(
    priced: Table,
) -> None:
    priced.update(cheap=0.001, dear=0.01)
    budget = ConnectionBudget(Budget(max_cost_usd=0.05))
    with budget.bound(), charged("open_browser", ["cheap", "dear"], BrowserRequest(timeout=120), ANY):
        assert budget.status()["reserved_usd"] == pytest.approx(0.02)
    unknown = charged("open_browser", ["cheap", "dear"], BrowserRequest(timeout=None), ANY)
    with budget.bound(), pytest.raises(BudgetExceededError) as refused, unknown:
        pass
    assert refused.value.skipped == {"open_browser:cheap": SKIP_PRICE_UNKNOWN}, "no lifetime, no price"
    assert budget.status()["spent_usd"] == pytest.approx(0.02)


def test_a_free_browser_needs_no_lifetime(priced: Table) -> None:
    priced.update(local=0.0)
    budget = ConnectionBudget(Budget(max_cost_usd=0.05))
    with budget.bound(), charged("open_browser", ["local"], BrowserRequest(), ANY):
        pass
    assert budget.status()["spent_usd"] == 0.0

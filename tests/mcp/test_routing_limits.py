"""The MCP budget's edges: what a browser and a job cost and wait, what the connection spent, and bad limits.

As in :mod:`tests.mcp.test_routing_tools`, over the fake members with the test's own prices.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import FakeBrowserProvider, FakeResearchProvider
from tests.mcp.conftest import CONFIG, Table, ok_on, outcome, routed
from tests.test_auto_seam import MEMBERS
from web_router import RouteDecision, RouteRequest
from web_router.constants_budget import SKIP_PRICE_UNKNOWN
from web_router.constants_mcp import (
    ENV_MCP_MAX_COST_USD,
    ENV_MCP_ROUTER,
    ENV_MCP_TIME_LIMIT_S,
    MCP_TOOL_BUDGET_START,
    MCP_TOOL_BUDGET_STATUS,
    MCP_TOOL_WEB_RUN,
)
from web_router.exceptions import BudgetExceededError, ConfigurationError
from web_router.mcp.budget import ConnectionBudget, browser_charged, charged, wait_left
from web_router.mcp.routing import McpRouting
from web_router.mcp.tools import WebRouterTools
from web_router.providers._deadline import current_deadline
from web_router.providers.auto_browser import AutoBrowserProvider
from web_router.registry import (
    register_browser_provider,
    register_research_provider,
    unregister_browser_provider,
    unregister_research_provider,
)
from web_router.routing import Budget
from web_router.routing.attempts import Attempt
from web_router.routing.pool import current_pool
from web_router.routing.pricing import attempt_cost
from web_router.types import BrowserRequest

# -- startup and budget_start refuse what is not a limit ---------------------------


@pytest.mark.parametrize("value", ["", "   "])
@pytest.mark.parametrize("name", [ENV_MCP_MAX_COST_USD, ENV_MCP_TIME_LIMIT_S])
def test_a_ceiling_set_empty_stops_the_server_rather_than_running_uncapped(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        McpRouting.from_environ({name: value})


@pytest.mark.parametrize("bad", [math.inf, math.nan])
@pytest.mark.parametrize("name", ["max_cost_usd", "time_limit_s"])
async def test_budget_start_refuses_a_limit_that_is_not_finite(name: str, bad: float) -> None:
    limits: dict[str, Any] = {"max_cost_usd": 1.0, "time_limit_s": 10.0, name: bad}
    failed, message = await outcome(routed(), MCP_TOOL_BUDGET_START, **limits)
    assert failed and name in message


# -- what a browser costs ------------------------------------------------------------


def _browser(price: float, **options: Any) -> float | None:
    return attempt_cost(Attempt(tool="open_browser", provider="cheap", request=BrowserRequest(**options)))  # type: ignore[arg-type]


def test_a_browser_is_charged_whole_minutes(priced: Table) -> None:
    priced.update(cheap=0.01)
    assert _browser(0.01, timeout=61) == pytest.approx(0.02), "61 seconds bill two minutes"
    assert _browser(0.01, timeout=120, headless=True, idle_timeout=30) == pytest.approx(0.02)


@pytest.mark.parametrize("extra", [{"proxy_country": "us"}, {"stealth": "low"}, {"solve_captchas": True}])
def test_a_browser_option_that_can_bill_apart_makes_the_price_unknown(priced: Table, extra: dict[str, Any]) -> None:
    priced.update(cheap=0.01, local=0.0)
    assert _browser(0.01, timeout=60, **extra) is None
    free = Attempt(tool="open_browser", provider="local", request=BrowserRequest(timeout=60, **extra))  # type: ignore[arg-type]
    assert attempt_cost(free) == 0.0, "a free backend bills no extra"


@pytest.fixture
def browsers() -> Iterator[None]:
    """``unpriced``, which maps no ``headless``, and ``cheap``, which maps everything."""

    class Unpriced(FakeBrowserProvider):
        name = "unpriced"
        supported_parameters = FakeBrowserProvider.supported_parameters - {"headless"}

    class Cheap(FakeBrowserProvider):
        name = "cheap"

    register_browser_provider("unpriced", Unpriced)
    register_browser_provider("cheap", Cheap)
    yield
    unregister_browser_provider("unpriced")
    unregister_browser_provider("cheap")


@pytest.mark.usefixtures("browsers")
def test_a_browser_open_prices_only_the_members_that_can_serve_it(priced: Table) -> None:
    priced.update(cheap=0.01)
    auto = AutoBrowserProvider(chain=(("unpriced", {}), ("cheap", {})))
    router: Any = SimpleNamespace(get_browser_provider=lambda name: auto)
    budget = ConnectionBudget(Budget(max_cost_usd=1.0))
    with budget.bound(), browser_charged(router, {"headless": True, "timeout": 60}):
        assert budget.status()["reserved_usd"] == pytest.approx(0.01)
    with budget.bound(), pytest.raises(BudgetExceededError) as refused, browser_charged(router, {"timeout": 60}):
        pass
    assert refused.value.skipped == {"open_browser:unpriced": SKIP_PRICE_UNKNOWN}, "both may open it"


# -- the clock around a call and a job's wait ----------------------------------------


def test_a_charged_call_runs_on_the_shared_clock(priced: Table) -> None:
    priced.update(cheap=0.0)
    budget = ConnectionBudget(Budget(time_limit_s=30))
    with budget.bound(), charged("run_template", ["cheap"], BrowserRequest()):
        deadline, pool = current_deadline(), current_pool()
        assert deadline is not None and pool is not None and deadline.at == pool.expires_at()


def test_a_job_tool_waits_no_longer_than_the_budget_has_left() -> None:
    assert wait_left(25.0) == 25.0, "no budget, the tool's own wait"
    budget = ConnectionBudget(Budget(time_limit_s=2))
    with budget.bound():
        assert wait_left(25.0) <= 2.0
        assert wait_left(1.0) == 1.0


# -- web_run on a job -----------------------------------------------------------------


def route_to_research(req: RouteRequest) -> RouteDecision:
    """An operator's router that always researches."""
    return RouteDecision(tools=["research"], reason="operator")


@pytest.fixture
def slow_research() -> Iterator[None]:
    """``first``, ``second`` and ``third`` for research, each a run that is still going after the wait."""

    class Slow(FakeResearchProvider):
        polls_before_done = 10**6

    for name in MEMBERS:
        register_research_provider(name, type(f"Slow_{name}", (Slow,), {"name": name}))
    yield
    for name in MEMBERS:
        unregister_research_provider(name)


@pytest.mark.usefixtures("slow_research")
async def test_web_run_hands_back_a_handle_for_a_job_still_running() -> None:
    routing = McpRouting.from_environ({ENV_MCP_ROUTER: f"{__name__}:route_to_research"})
    tools = WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, research_wait=0.2)
    # Bounded, so a web_run that waits out the whole run fails here rather than hanging.
    pending = await asyncio.wait_for(ok_on(tools, MCP_TOOL_WEB_RUN, prompt="what changed in solar policy?"), 5)
    assert pending["tool"] == "research" and pending["decision"]["reason"] == "operator"
    polled = await ok_on(tools, "research_status", handle=pending["handle"])
    assert polled["handle"] == pending["handle"], "the run is polled, never started again"


# -- what the connection spent --------------------------------------------------------


async def test_budget_status_keeps_what_the_connection_spent_across_tasks(members: list[str], priced: Table) -> None:
    priced.update(first=0.04)
    for tools in (routed(), routed(Budget(max_cost_usd=1.0))):
        await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=0.5, time_limit_s=60)
        await ok_on(tools, "search", query="q", provider="first")
        await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=0.5, time_limit_s=60)
        await ok_on(tools, "search", query="q", provider="first")
        status = (await ok_on(tools, MCP_TOOL_BUDGET_STATUS))["budget"]
        assert status["spent_usd"] == 0.04 and status["connection"]["spent_usd"] == 0.08


async def test_a_task_opened_after_the_ceiling_ran_out_reports_no_time_not_negative_time() -> None:
    tools = routed(Budget(time_limit_s=0.01))
    time.sleep(0.05)
    status = (await ok_on(tools, MCP_TOOL_BUDGET_START, max_cost_usd=1.0, time_limit_s=60))["budget"]
    assert (status["time_limit_s"], status["time_left_s"]) == (0.0, 0.0)

"""The MCP budget's edges: what a browser and a job cost and wait, what the connection spent, and bad limits.

As in :mod:`tests.mcp.test_routing_tools`, over the fake members with the test's own prices.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import FakeBrowserProvider, FakeResearchProvider
from tests.mcp.conftest import CONFIG, Table, ok_on, outcome, routed
from tests.routing_helpers import MEMBERS
from web_router import RouteDecision, RouteRequest, prices
from web_router.constants_budget import SKIP_PRICE_UNKNOWN
from web_router.constants_mcp import (
    ENV_MCP_MAX_COST_USD,
    ENV_MCP_ROUTER,
    ENV_MCP_TIME_LIMIT_S,
    MCP_TOOL_BUDGET_START,
    MCP_TOOL_BUDGET_STATUS,
    MCP_TOOL_WEB_RUN,
)
from web_router.exceptions import BudgetExceededError, ConfigurationError, DeadlineExceededError
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
from web_router.types import BrowserRequest, ResearchProgress, ResearchRequest

ANY = object()
"""Any router: the connection's budget has no owner, so it holds every router's calls."""


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


def _browser(**options: Any) -> float | None:
    return attempt_cost(Attempt(tool="open_browser", provider="cheap", request=BrowserRequest(**options)))  # type: ignore[arg-type]


def test_a_browser_is_charged_whole_minutes(priced: Table) -> None:
    priced.update(cheap=0.01)
    assert _browser(timeout=61) == pytest.approx(0.02), "61 seconds bill two minutes"
    assert _browser(timeout=120, headless=True, idle_timeout=30) == pytest.approx(0.02)


@pytest.mark.parametrize("extra", [{"proxy_country": "us"}, {"stealth": "low"}, {"solve_captchas": True}])
def test_a_browser_option_that_can_bill_apart_makes_the_price_unknown(priced: Table, extra: dict[str, Any]) -> None:
    priced.update(cheap=0.01, local=0.0)
    assert _browser(timeout=60, **extra) is None
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
    with budget.bound(), charged("run_template", ["cheap"], BrowserRequest(), ANY):
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


class _LostInPoll(FakeResearchProvider):
    """A run that is accepted, then whose poll runs out of the call's deadline."""

    polls_before_done = 10**6

    def _fetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        raise DeadlineExceededError(self.name, 0.1)

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        return self._fetch(request, job_id)


@pytest.fixture
def lost_in_poll() -> Iterator[None]:
    for name in MEMBERS:
        register_research_provider(name, type(f"Lost_{name}", (_LostInPoll,), {"name": name}))
    yield
    for name in MEMBERS:
        unregister_research_provider(name)


@pytest.mark.usefixtures("lost_in_poll")
async def test_web_run_keeps_the_handle_of_a_run_whose_poll_ran_out_of_time() -> None:
    routing = McpRouting.from_environ({ENV_MCP_ROUTER: f"{__name__}:route_to_research"})
    tools = WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, research_wait=0.2)
    pending = await asyncio.wait_for(ok_on(tools, MCP_TOOL_WEB_RUN, prompt="what changed in solar policy?"), 5)
    assert pending["tool"] == "research" and pending["job_id"].startswith("first")
    assert tools.store.get(pending["handle"]).job is not None, "the paid run's handle is kept"


@pytest.mark.usefixtures("slow_research")
async def test_a_pending_note_says_how_long_the_tool_actually_waited() -> None:
    routing = McpRouting(ceiling=Budget(time_limit_s=0.3))
    tools = WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, research_wait=25.0)
    pending = await asyncio.wait_for(ok_on(tools, "research", query="q", provider="first"), 5)
    waited = float(re.search(r"in the ([0-9.e-]+)s this tool waits", pending["note"]).group(1))  # type: ignore[union-attr]
    assert waited <= 0.3, "cut to the time the budget had left, not the tool's 25 s"


def test_a_browser_with_a_charge_per_session_on_top_is_a_floor_and_unknown_under_a_cap() -> None:
    row = prices.price("anchor", "open_browser")
    assert row is not None and row.estimate_is_floor, "its note names a charge per browser created"
    attempt = Attempt(tool="open_browser", provider="anchor", request=BrowserRequest(timeout=60))  # type: ignore[arg-type]
    assert attempt_cost(attempt) is None


@pytest.mark.parametrize("bad", [math.inf, -1, 0])
async def test_web_run_refuses_a_cap_that_is_not_a_finite_positive_number(members: list[str], bad: float) -> None:
    failed, message = await outcome(routed(), MCP_TOOL_WEB_RUN, prompt="q", max_cost_usd=bad)
    assert failed and "max_cost_usd" in message and members == []


@pytest.mark.parametrize("value", ["inf", "Infinity", "-inf", "nan"])
def test_a_ceiling_that_is_not_finite_stops_the_server(value: str) -> None:
    with pytest.raises(ConfigurationError):
        McpRouting.from_environ({ENV_MCP_MAX_COST_USD: value})


async def route_slowly(req: RouteRequest) -> RouteDecision:
    """An operator's router that takes longer than the call's own time limit to answer."""
    await asyncio.sleep(0.3)
    return RouteDecision(tools=["search"])


route_slowly.timeout_s = 5.0  # type: ignore[attr-defined]


async def test_web_runs_time_limit_covers_its_planning(members: list[str]) -> None:
    tools = routed(**{ENV_MCP_ROUTER: f"{__name__}:route_slowly"})
    failed, message = await outcome(tools, MCP_TOOL_WEB_RUN, prompt="q", time_limit_s=0.2)
    assert failed and "deadline" in message.lower() and members == [], "the time went on planning: none is left"


@pytest.fixture
def nimble_sent() -> Iterator[list[ResearchRequest]]:
    """A fake research backend registered as ``nimble``, so the real price list and effort table apply to it."""
    sent: list[ResearchRequest] = []

    class SentNimble(FakeResearchProvider):
        name = "nimble"

        def _start(self, request: ResearchRequest) -> ResearchProgress:
            sent.append(request)
            return super()._start(request)

    register_research_provider("nimble", SentNimble)
    yield sent
    unregister_research_provider("nimble")


async def test_the_research_tool_under_a_ceiling_runs_at_the_effort_the_budget_maps(
    nimble_sent: list[ResearchRequest],
) -> None:
    routing = McpRouting(ceiling=Budget(max_cost_usd=0.10))
    tools = WebRouterTools(routing.build(provider_config={"auto": {"chain": ["nimble"]}}), routing=routing)
    await ok_on(tools, "research", query="q")
    assert [request.effort for request in nimble_sent] == ["low"], "nimble's default has no price: it would refuse"

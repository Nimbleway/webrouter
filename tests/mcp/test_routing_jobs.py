"""web_run on a job, and a job's start under a budget: the handle it hands back, the wait it reports, the effort.

As in :mod:`tests.mcp.test_routing_tools`, over the fake members with the test's own prices.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from tests.conftest import FakeResearchProvider
from tests.mcp.conftest import CONFIG, ok_on
from tests.routing_helpers import MEMBERS
from web_router import AsyncWebRouter, RouteDecision, RouteRequest, WebRouter
from web_router.constants_mcp import ENV_MCP_ROUTER, MCP_TOOL_WEB_RUN
from web_router.exceptions import DeadlineExceededError
from web_router.mcp.routing_config import McpRouting
from web_router.mcp.tools import WebRouterTools
from web_router.registry import register_research_provider, unregister_research_provider
from web_router.routing import Budget
from web_router.types import ResearchProgress, ResearchRequest


def route_to_research(req: RouteRequest) -> RouteDecision:
    """An operator's router that always researches."""
    return RouteDecision(tools=["research"], reason="operator")


def _researching(**options: Any) -> WebRouterTools:
    """A session whose ``web_run`` always researches, over the fake members."""
    routing = McpRouting.from_environ({ENV_MCP_ROUTER: f"{__name__}:route_to_research"})
    return WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, **options)


def _as_members(cls: type[FakeResearchProvider]) -> Iterator[None]:
    """``cls`` registered for research as ``first``, ``second`` and ``third``, for the test."""
    for name in MEMBERS:
        register_research_provider(name, type(f"{cls.__name__}_{name}", (cls,), {"name": name}))
    yield
    for name in MEMBERS:
        unregister_research_provider(name)


class _Slow(FakeResearchProvider):
    polls_before_done = 10**6


@pytest.fixture
def slow_research() -> Iterator[None]:
    """Each member a run that is still going after the wait."""
    yield from _as_members(_Slow)


# -- a job still going hands back a handle ---------------------------------------------


@pytest.mark.usefixtures("slow_research")
async def test_web_run_hands_back_a_handle_for_a_job_still_running() -> None:
    tools = _researching(research_wait=0.2)
    # Bounded, so a web_run that waits out the whole run fails here rather than hanging.
    pending = await asyncio.wait_for(ok_on(tools, MCP_TOOL_WEB_RUN, prompt="what changed in solar policy?"), 5)
    assert pending["tool"] == "research" and pending["decision"]["reason"] == "operator"
    assert pending["decision"]["router"] == "operator", "named as capabilities name it, not route_to_research"
    polled = await ok_on(tools, "research_status", handle=pending["handle"])
    assert polled["handle"] == pending["handle"], "the run is polled, never started again"


class _BothLostInPoll(FakeResearchProvider):
    """Runs that each take their own id, then whose polls run out of the deadline once two have started."""

    started: ClassVar[list[str]] = []
    both = asyncio.Event()

    def _start(self, request: ResearchRequest) -> ResearchProgress:
        self.started.append(request.query)
        if len(self.started) == 2:
            self.both.set()
        return ResearchProgress(job_id=f"run-{request.query}", state="running")

    async def _afetch(self, request: ResearchRequest, job_id: str) -> ResearchProgress:
        await self.both.wait()
        raise DeadlineExceededError(self.name, 0.1)


@pytest.fixture
def both_lost_in_poll() -> Iterator[None]:
    _BothLostInPoll.started, _BothLostInPoll.both = [], asyncio.Event()
    yield from _as_members(_BothLostInPoll)


@pytest.mark.usefixtures("both_lost_in_poll")
async def test_two_runs_whose_polls_run_out_at_once_each_keep_their_own_handle() -> None:
    tools = _researching(research_wait=5)
    runs = (ok_on(tools, MCP_TOOL_WEB_RUN, prompt=prompt) for prompt in ("alpha", "beta"))
    alpha, beta = await asyncio.wait_for(asyncio.gather(*runs), 5)
    assert alpha["job_id"].endswith("run-alpha") and beta["job_id"].endswith("run-beta")
    assert tools.store.get(alpha["handle"]).job is not None, "the paid run's handle is kept"


@pytest.mark.usefixtures("slow_research")
async def test_a_pending_note_says_how_long_the_tool_actually_waited() -> None:
    routing = McpRouting(ceiling=Budget(time_limit_s=0.3))
    tools = WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, research_wait=25.0)
    pending = await asyncio.wait_for(ok_on(tools, "research", query="q", provider="first"), 5)
    assert _waited(pending) <= 0.3, "cut to the time the budget had left, not the tool's 25 s"


@pytest.mark.usefixtures("slow_research")
async def test_a_pending_note_from_web_run_counts_its_own_time_limit() -> None:
    tools = _researching(research_wait=25.0)
    pending = await asyncio.wait_for(ok_on(tools, MCP_TOOL_WEB_RUN, prompt="q", time_limit_s=0.3), 5)
    assert _waited(pending) <= 0.3, "cut to web_run's own time limit, not the tool's 25 s"


def _waited(pending: dict[str, Any]) -> float:
    return float(re.search(r"in the ([0-9.e-]+)s this tool waits", pending["note"]).group(1))  # type: ignore[union-attr]


# -- the effort a budget maps ---------------------------------------------------------


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


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_a_submitted_runs_answer_records_the_effort_the_budget_mapped(
    nimble_sent: list[ResearchRequest], asynchronous: bool
) -> None:
    if asynchronous:
        async with AsyncWebRouter() as client, client.budget(Budget(max_cost_usd=0.10)):
            job = await client.submit_research("q", provider="nimble")
            answer, again = await job.aresult(), await job.aresult()
    else:
        with WebRouter() as client, client.budget(Budget(max_cost_usd=0.10)):
            job = client.submit_research("q", provider="nimble")
            answer, again = job.result(), job.result()
    assert [request.effort for request in nimble_sent] == ["low"] and again is answer, "copied once, then kept"
    assert answer.decision is not None and answer.decision.params == {"effort": "low"}, "as research() records it"

"""The built-in rules router: what it picks for a prompt, and that the routers attach it by default for plan and run."""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.plan_helpers import called_fixture  # noqa: F401  (fixture)
from tests.routing_helpers import wr
from web_router import AsyncWebRouter, RouteRequest, WebRouter
from web_router.constants_routing import RULES_REASON_NO_URL, RULES_REASON_RESEARCH, RULES_REASON_URL
from web_router.routing.registry import available_routers
from web_router.routing.rules import RulesRouter, rules

RESEARCH = [
    "Compare the iPhone 16 and Pixel 9 cameras",
    "compare postgres vs mysql for analytics",
    "Research the best CRMs for startups",
    "Can you research how tariffs affect EU steel?",
    "please research which CRMs suit startups",
    "Write me a short report on lithium prices",
    "Give me a report on lithium prices",
    "Analyze the market for home batteries in Europe",
    "prepare an investor briefing on Nvidia",
    "Give me an in-depth overview of RAG",
    "a deep dive into WebGPU adoption",
    "pros and cons of nuclear power",
    "a literature review of sleep and memory",
    "market research on EV chargers in Spain",
    "competitive landscape for vector databases",
    "a comprehensive guide to Kubernetes networking",
]

SEARCH = [
    "What is the capital of France?",
    "How does a heat pump work?",
    "Why is the sky blue?",
    "How did Napoleon die?",
    "Find the population of Tokyo",
    "Is there any vegan restaurant near me?",
    "list of US presidents",
    "Which teams won the Stanley Cup in the 1990s?",
    "Track my package 1Z999",
    "Write a poem about cats",
    "Open source alternatives to Slack",
    "Sign in to my bank and download statements",
    "What is the analysis of variance?",
    "What is market research?",
    "what are the pros and cons of a literature review",
    "Define competitive analysis",
    "How do I compare two columns in Excel and return the matching rows?",
    "how to write a report in LaTeX",
    "Can you analyze this sentence for its subject and verb?",
    "research papers on sleep and memory",
    "what is a research report",
    "reporters near me",
    "compare",
    "Is Node.js single threaded?",
    "Quel est le prix du bitcoin ?",
    "",
]


def _req(prompt: str, urls: tuple[str, ...] = ()) -> RouteRequest:
    return RouteRequest(prompt=prompt, candidates=(), deadline_s=0.05, urls=urls)


@pytest.mark.parametrize("prompt", RESEARCH)
def test_a_clear_ask_for_research_researches_then_searches(prompt: str) -> None:
    assert rules(_req(prompt)).tools == ["research", "search"]
    assert rules(_req(prompt)).reason == RULES_REASON_RESEARCH


@pytest.mark.parametrize("prompt", SEARCH)
def test_anything_else_searches(prompt: str) -> None:
    decision = rules(_req(prompt))
    assert (decision.tools, decision.reason) == (["search"], RULES_REASON_NO_URL)


def test_a_url_extracts_before_any_research_words() -> None:
    decision = rules(_req("compare https://a.example and https://b.example", ("https://a.example",)))
    assert (decision.tools, decision.reason) == (["extract"], RULES_REASON_URL)


def test_the_pattern_stays_fast_on_a_long_prompt() -> None:
    start = time.perf_counter()
    for prompt in ("please " * 50_000 + "x", "compare " + "a " * 100_000, "write a " + "x-y " * 50_000):
        rules(_req(prompt))
    assert time.perf_counter() - start < 1.0


def test_by_name_it_is_asked_by_plan_and_run_alone() -> None:
    assert "rules" in available_routers()
    built = RulesRouter()
    assert (built.route_tools, built.router_name) == ((), "rules")


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
def test_the_routers_attach_it_by_default_and_none_attaches_nothing(cls: type) -> None:
    default, bare = cls(), cls(router=None)
    try:
        assert default._router is not None and default._router.name == "rules"
        assert default._route_tools == frozenset(), "search(), extract() and the rest never ask it"
        assert bare._router is None
    finally:
        for router in (default, bare):
            router.close() if cls is WebRouter else asyncio.run(router.aclose())


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize(
    ("prompt", "tool"),
    [("compare A and B across Europe", "research"), ("who won the 2022 world cup", "search")],
)
def test_plan_follows_it(prompt: str, tool: str) -> None:
    plan = wr(router="rules").plan(prompt)
    assert (plan.tool, plan.decision.router) == (tool, "rules")


@pytest.mark.usefixtures("called")
def test_plan_reads_a_url_with_it_and_searches_with_no_router() -> None:
    prompt = "read https://example.com/a"
    assert wr(router="rules").plan(prompt).tool == "extract"
    assert wr().plan(prompt).tool == "search"

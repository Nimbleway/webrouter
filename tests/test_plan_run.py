"""``plan()`` and ``run()``: a prompt routed to one method, its options and its backends, then run.

Every method's ``auto`` runs over three fake members, ``first`` and ``second``
failing before they send anything and ``third`` answering, so the members
called show the order the run took.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, TOOLS
from tests.routing_helpers import member as _member
from tests.routing_helpers import wr as _wr
from web_router import AsyncWebRouter, Budget, Plan, RouteDecision, RouteRequest
from web_router.exceptions import MissingParameterError
from web_router.types import BrowseResponse, ExtractResponse, ResearchResponse, SearchResponse
from web_router.types_answer import AnswerResponse

URL = "https://example.com/report"
PROMPT = f"what does {URL} say about 2024?"
RESPONSE = {
    "search": SearchResponse,
    "extract": ExtractResponse,
    "answer": AnswerResponse,
    "research": ResearchResponse,
    "browse": BrowseResponse,
}
PARAMS = {
    "search": {"query": PROMPT},
    "extract": {"urls": [URL]},
    "answer": {"query": PROMPT},
    "research": {"query": PROMPT},
    "browse": {"task": PROMPT, "start_url": URL},
}


@pytest.fixture
def called() -> Iterator[list[str]]:
    """``first``, ``second`` and ``third`` registered for every method; what was called, as ``tool:member``."""
    log: list[str] = []
    for tool in TOOLS:
        seen: list[str] = []
        log_for = _Tagged(log, tool.name, seen)
        for name in MEMBERS:
            tool.register(name, _member(tool, name, log_for, fails=name != "third"))
    yield log
    for tool in TOOLS:
        for name in MEMBERS:
            tool.unregister(name)


class _Tagged(list):  # type: ignore[type-arg]
    """A log that records each member as ``tool:member`` in the shared one."""

    def __init__(self, shared: list[str], tool: str, own: list[str]) -> None:
        super().__init__()
        self.shared, self.tool = shared, tool

    def append(self, name: str) -> None:  # type: ignore[override]
        self.shared.append(f"{self.tool}:{name}")


def _chooser(tool: str | None, **decision: Any) -> Any:
    """A router that picks ``tool``, ranks ``third`` first for it, and keeps what it was shown."""

    def route(req: RouteRequest) -> RouteDecision:
        route.seen.append(req)  # type: ignore[attr-defined]
        tools = None if tool is None else [tool]
        return RouteDecision(tools=tools, providers={tool: ["third"]} if tool else None, reason="picked", **decision)

    route.seen = []  # type: ignore[attr-defined]
    return route


# -- plan ----------------------------------------------------------------------


@pytest.mark.usefixtures("called")
def test_with_no_router_the_plan_is_search_on_the_static_order() -> None:
    plan = _wr().plan(PROMPT)
    assert (plan.tool, dict(plan.params)) == ("search", {"query": PROMPT})
    assert list(plan.providers["search"]) == list(MEMBERS)
    assert plan.decision.router is None and plan.decision.reason is None
    assert plan.decision.merged == [f"search:{name}" for name in MEMBERS]


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("tool", list(PARAMS))
def test_with_no_router_a_pinned_tool_is_planned_with_its_params(tool: str) -> None:
    plan = _wr().plan(PROMPT, tool=tool)
    assert (plan.tool, dict(plan.params)) == (tool, PARAMS[tool])
    assert list(plan.providers[tool]) == list(MEMBERS)


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("tool", list(PARAMS))
def test_the_router_picks_each_tool_and_the_plan_carries_its_params(tool: str) -> None:
    router = _chooser(tool)
    plan = _wr(router=router).plan(PROMPT)
    assert (plan.tool, dict(plan.params)) == (tool, PARAMS[tool])
    assert plan.providers[tool][0] == "third"
    assert (plan.decision.reason, plan.decision.router) == ("picked", router.__qualname__)
    (req,) = router.seen
    assert req.pinned_tool is None and req.urls == (URL,)
    assert {c.tool for c in req.candidates} == set(PARAMS), "every method is offered"


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("router", [None, "extract"], ids=["no-router", "router-without-urls"])
def test_a_pinned_extract_with_no_url_is_refused_at_plan_time(router: str | None) -> None:
    wr = _wr(router=None if router is None else _chooser(router))
    with pytest.raises(MissingParameterError, match="no http\\(s\\) URL"):
        wr.plan("summarize the latest report", tool="extract")
    with pytest.raises(MissingParameterError):
        wr.run("summarize the latest report", tool="extract")


@pytest.mark.usefixtures("called")
def test_a_pinned_extract_whose_router_supplies_a_url_is_planned() -> None:
    def route(req: RouteRequest) -> RouteDecision:
        return RouteDecision(params={"extract": {"urls": ["https://example.com/r"]}})

    plan = _wr(router=route).plan("summarize the latest report", tool="extract")
    assert (plan.tool, plan.params["urls"]) == ("extract", ["https://example.com/r"])


@pytest.mark.usefixtures("called")
def test_with_no_url_a_router_that_picks_extract_without_urls_gets_search() -> None:
    router = _chooser("extract")
    plan = _wr(router=router).plan("summarize the latest report")
    assert "extract" in {c.tool for c in router.seen[0].candidates}, "offered: a router may supply the URLs"
    assert plan.tool == "search" and plan.params == {"query": "summarize the latest report"}


@pytest.mark.usefixtures("called")
def test_a_url_named_twice_is_planned_once() -> None:
    plan = _wr().plan(f"compare {URL} with {URL}.", tool="extract")
    assert plan.params["urls"] == [URL]


@pytest.mark.usefixtures("called")
def test_a_router_failure_plans_the_static_order() -> None:
    plan = _wr(router=lambda req: 1 / 0).plan(PROMPT)
    assert (plan.tool, plan.decision.reason) == ("search", "router_error")


@pytest.mark.usefixtures("called")
def test_a_budget_maps_research_effort_into_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_router.routing import decide
    from web_router.routing.params import EffortChoice

    monkeypatch.setattr(decide, "map_effort", lambda *_: EffortChoice(effort="low", source="budget", reason="r"))
    plan = _wr(router=_chooser("research")).plan(PROMPT, budget=Budget(time_limit_s=600.0))
    assert dict(plan.params) == {"query": PROMPT, "effort": "low"}
    assert plan.decision.params_source == "budget"


# -- run -----------------------------------------------------------------------


@pytest.mark.parametrize("tool", list(PARAMS))
def test_run_of_a_plan_returns_the_methods_own_response_with_the_record(tool: str, called: list[str]) -> None:
    wr = _wr(router=_chooser(tool))
    plan = wr.plan(PROMPT)
    response = wr.run(plan)
    assert isinstance(response, RESPONSE[tool])
    assert called[0] == f"{tool}:third", "run in the plan's order"
    assert response.decision == plan.decision


@pytest.mark.parametrize("tool", list(PARAMS))
def test_run_of_a_prompt_plans_and_runs_in_one_step(tool: str, called: list[str]) -> None:
    response = _wr(router=_chooser(tool)).run(PROMPT)
    assert isinstance(response, RESPONSE[tool])
    assert called[0] == f"{tool}:third"
    assert response.decision.reason == "picked"


def test_run_of_a_plan_made_by_hand_uses_its_order(called: list[str]) -> None:
    plan = Plan(prompt="q", tool="answer", params={"query": "q"}, providers={"answer": ["third", "first"]})
    response = _wr().run(plan)
    assert isinstance(response, AnswerResponse)
    assert called == ["answer:third"]


def test_run_with_a_budget_records_the_spend(called: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from web_router import prices

    row = SimpleNamespace(estimate_usd=0.001, estimate_is_floor=False, mode=None)
    monkeypatch.setattr(prices, "price", lambda *_: row)
    monkeypatch.setattr(prices, "estimate_latency", lambda *_: None)
    response = _wr(router=_chooser("search")).run(PROMPT, budget=Budget(max_cost_usd=0.01))
    assert called == ["search:third"]
    assert response.decision.reason == "picked" and response.decision.estimated_cost_usd == 0.001


async def test_async_plan_and_run_route_as_the_sync_ones_do(called: list[str]) -> None:
    wr = _wr(AsyncWebRouter, router=_chooser("answer"))
    plan = await wr.plan(PROMPT)
    assert plan.tool == "answer"
    response = await wr.run(plan)
    assert isinstance(response, AnswerResponse) and called[0] == "answer:third"
    assert isinstance(await wr.run(PROMPT), AnswerResponse)


def test_a_plans_repr_shows_neither_its_prompt_nor_a_whole_url() -> None:
    secret = "https://user:pw@example.com/p?token=SECRET"
    plan = Plan(prompt=f"fetch {secret} now", tool="extract", params={"urls": [secret]}, providers={"extract": ["x"]})
    shown = repr(plan)
    assert "SECRET" not in shown and "pw@" not in shown and "fetch" not in shown
    assert "https://example.com" in shown and "'extract'" in shown
    searched = Plan(prompt=f"fetch {secret}", tool="search", params={"query": f"fetch {secret}"})
    assert "SECRET" not in repr(searched)

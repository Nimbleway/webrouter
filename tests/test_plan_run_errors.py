"""``plan()`` where it refuses, falls back or sets something aside: no URL to read, a failed router, the notes."""

from __future__ import annotations

from typing import Any

import pytest

from tests.plan_helpers import PROMPT, URL, called_fixture, chooser  # noqa: F401  (fixture)
from tests.routing_helpers import wr as _wr
from web_router import Budget, Plan, RouteDecision, RouteRequest, WebRouter
from web_router.exceptions import MissingParameterError


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("router", [None, "extract"], ids=["no-router", "router-without-urls"])
def test_a_pinned_extract_with_no_url_is_refused_at_plan_time(router: str | None) -> None:
    wr = _wr(router=None if router is None else chooser(router))
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
    router = chooser("extract")
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


def test_a_plans_repr_shows_neither_its_prompt_nor_a_whole_url() -> None:
    secret = "https://user:pw@example.com/p?token=SECRET"
    plan = Plan(prompt=f"fetch {secret} now", tool="extract", params={"urls": [secret]}, providers={"extract": ["x"]})
    shown = repr(plan)
    assert "SECRET" not in shown and "pw@" not in shown and "fetch" not in shown
    assert "https://example.com" in shown and "'extract'" in shown
    searched = Plan(prompt=f"fetch {secret}", tool="search", params={"query": f"fetch {secret}"})
    assert "SECRET" not in repr(searched)


def test_a_plan_whose_router_picks_a_method_the_budget_emptied_notes_why() -> None:
    from web_router.constants_merge import MSG_TOOL_NO_CANDIDATE

    router = WebRouter(router=lambda req: RouteDecision(tools=["extract"]), url_policy="public_only")
    plan = router.plan("read https://example.com/", budget=Budget(max_cost_usd=0.0001))
    assert plan.tool == "search"
    assert plan.decision.ignored == {"extract": MSG_TOOL_NO_CANDIDATE.format(tool="extract")}


@pytest.mark.usefixtures("called")
def test_a_plan_notes_a_router_url_the_policy_refused_when_it_searches_instead() -> None:
    from web_router.constants_merge import NOTE_EXTRACT_URLS

    route = lambda req: RouteDecision(tools=["extract"], params={"extract": {"urls": ["http://10.0.0.1/x"]}})  # noqa: E731
    plan = _wr(router=route, url_policy="public_only").plan("summarize it")
    assert plan.tool == "search"
    note = (plan.decision.ignored or {})[NOTE_EXTRACT_URLS]
    assert "10.0.0.1" in note and "/x" not in note


def _urls_router(n: int) -> Any:
    urls = [f"https://site.test/{i}" for i in range(n)]
    return lambda req: RouteDecision(tools=["extract"], params={"extract": {"urls": urls}})


@pytest.mark.usefixtures("called")
def test_a_plan_whose_router_urls_are_dropped_for_cost_searches_instead(priced: Any) -> None:
    from web_router.constants_merge import MSG_EXTRACT_NO_URL, NOTE_EXTRACT

    priced.update(first=(0.01, None), second=(0.01, None), third=(0.01, None))
    cap = Budget(max_cost_usd=0.05)
    plan = _wr(router=_urls_router(50)).plan("compare these pages", budget=cap)
    assert (plan.tool, plan.params) == ("search", {"query": "compare these pages"})
    ignored = plan.decision.ignored or {}
    assert ignored[NOTE_EXTRACT] == MSG_EXTRACT_NO_URL and "cost cap" in ignored["params.extract.urls"]
    one = _wr(router=_urls_router(1)).plan("compare these pages", budget=cap)
    assert (one.tool, one.params["urls"]) == ("extract", ["https://site.test/0"])


@pytest.mark.parametrize("budget", [None, Budget(max_cost_usd=1.0)], ids=["unbudgeted-run", "budgeted-run"])
def test_a_plan_the_budget_left_no_backend_never_calls_one(
    called: list[str], priced: Any, budget: Budget | None
) -> None:
    from web_router import BudgetExceededError

    priced.update(first=(0.05, None), second=(0.05, None), third=(0.05, None))
    wr = _wr()
    plan = wr.plan(PROMPT, budget=Budget(max_cost_usd=0.01))
    assert plan.tool == "search" and list(plan.providers["search"]) == []
    with pytest.raises(BudgetExceededError, match="search:first skipped"):
        wr.run(plan, budget=budget)
    assert called == []


@pytest.mark.usefixtures("called")
def test_a_plan_with_an_empty_order_and_no_budget_drop_runs_no_backend(called: list[str]) -> None:
    from web_router.exceptions import WebRouterError

    plan = Plan(prompt="q", tool="search", params={"query": "q"}, providers={"search": ()})
    with pytest.raises(WebRouterError):
        _wr().run(plan)
    assert called == []

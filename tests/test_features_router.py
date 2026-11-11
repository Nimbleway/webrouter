"""The built-in ``features`` router: the method table, the decision it answers, and its place as the default."""

from __future__ import annotations

import itertools

import pytest

from tests.plan_helpers import called_fixture  # noqa: F401  (fixture)
from tests.routing_helpers import MEMBERS
from web_router import AsyncWebRouter, Candidate, FeaturesRouter, RouteDecision, RouteRequest, WebRouter, read_features
from web_router.routing.features import FEATURE_MAPPING, Features, Verb
from web_router.routing.registry import available_routers


def _request(prompt: str, *, urls: tuple[str, ...] = (), pinned: str | None = None) -> RouteRequest:
    return RouteRequest(
        prompt=prompt,
        candidates=(Candidate(tool="search", provider="first"),),
        deadline_s=0.05,
        urls=urls,
        pinned_tool=pinned,  # type: ignore[arg-type]
    )


def _wr() -> WebRouter:
    return WebRouter(provider_config={"auto": {"chain": list(MEMBERS)}})


def test_the_table_covers_every_combination_of_features() -> None:
    every = {Features(*row) for row in itertools.product(Verb, [False, True], [False, True], [False, True])}
    assert set(FEATURE_MAPPING) == every


def test_the_table_never_picks_answer() -> None:
    assert set(FEATURE_MAPPING.values()) == {"search", "extract", "research", "browse"}


@pytest.mark.parametrize(
    ("features", "tool"),
    [
        (Features(Verb.QUESTION, has_url=False, plural=False, kw_research=False), "search"),
        (Features(Verb.QUESTION, has_url=False, plural=False, kw_research=True), "research"),
        (Features(Verb.QUESTION, has_url=True, plural=False, kw_research=False), "extract"),
        (Features(Verb.EXTRACT, has_url=True, plural=False, kw_research=False), "extract"),
        (Features(Verb.ACT, has_url=False, plural=False, kw_research=False), "browse"),
        (Features(Verb.NAVIGATE, has_url=False, plural=False, kw_research=False), "browse"),
        (Features(Verb.COMPILE, has_url=False, plural=True, kw_research=False), "research"),
        (Features(Verb.ANALYZE, has_url=False, plural=False, kw_research=True), "research"),
        (Features(Verb.OTHER, has_url=False, plural=False, kw_research=False), "search"),
        (Features(Verb.OTHER, has_url=False, plural=True, kw_research=False), "research"),
        (Features(Verb.OTHER, has_url=True, plural=False, kw_research=False), "extract"),
    ],
    ids=[
        "plain question",
        "question with a research word",
        "question about a url",
        "extract a url",
        "act",
        "navigate",
        "compile many",
        "analyze",
        "keywords",
        "keywords for many",
        "keywords with a url",
    ],
)
def test_the_table_picks_the_method(features: Features, tool: str) -> None:
    assert features.tool == tool


def test_read_features_reads_the_four_features() -> None:
    assert read_features("Add the cheapest plan on https://example.com/pricing to my cart") == Features(
        Verb.ACT, has_url=True, plural=False, kw_research=False
    )


def test_summarizing_one_url_is_an_extract_of_it() -> None:
    assert read_features("Summarize https://example.com/pricing").tool == "extract"


def test_a_question_searches_with_the_row_as_its_reason() -> None:
    decision = FeaturesRouter().route(_request("Who is the CEO of Example Corp?"))
    assert decision == RouteDecision(
        tools=["search"], reason="features: verb=question has_url=False plural=False kw_research=False"
    )


def test_a_url_is_extracted_and_browsed_from() -> None:
    url = "https://example.com/pricing"
    decision = FeaturesRouter().route(_request(f"What does {url} say?", urls=(url,)))
    assert decision.tools == ["extract"]
    assert decision.params == {"extract": {"urls": [url]}, "browse": {"start_url": url}}


def test_a_bare_host_is_the_url_when_the_plan_read_none() -> None:
    decision = FeaturesRouter().route(_request("Go to nfl.com and find the standings"))
    assert decision.tools == ["browse"]
    assert decision.params == {"extract": {"urls": ["https://nfl.com"]}, "browse": {"start_url": "https://nfl.com"}}


def test_a_pinned_method_is_left_as_pinned() -> None:
    assert FeaturesRouter().route(_request("Who is the CEO of Example Corp?", pinned="research")) == RouteDecision()


def test_it_is_a_built_in_router_asked_by_plan_and_run_only() -> None:
    assert "features" in available_routers()
    assert FeaturesRouter.route_tools == ()


@pytest.mark.usefixtures("called")
def test_a_router_built_with_no_router_plans_with_features() -> None:
    with _wr() as wr:
        plan = wr.plan("Analyze the impact of tariffs on steel")
    assert plan.tool == "research"
    assert plan.decision.router == "features"
    assert plan.decision.reason == "features: verb=analyze has_url=False plural=False kw_research=True"


@pytest.mark.usefixtures("called")
def test_the_default_plans_a_bare_host_it_reads() -> None:
    with _wr() as wr:
        plan = wr.plan("Go to nfl.com and find the standings")
    assert (plan.tool, plan.params["start_url"]) == ("browse", "https://nfl.com")


@pytest.mark.usefixtures("called")
def test_router_none_still_plans_the_static_order() -> None:
    with WebRouter(provider_config={"auto": {"chain": list(MEMBERS)}}, router=None) as wr:
        plan = wr.plan("Analyze the impact of tariffs on steel")
    assert plan.tool == "search" and plan.decision.router is None


@pytest.mark.usefixtures("called")
def test_the_default_leaves_a_plain_call_unrouted() -> None:
    with _wr() as wr:
        response = wr.search("Analyze the impact of tariffs on steel")
    assert response.decision is None


@pytest.mark.usefixtures("called")
async def test_the_async_router_plans_with_features_too() -> None:
    async with AsyncWebRouter(provider_config={"auto": {"chain": list(MEMBERS)}}) as wr:
        plan = await wr.plan("List every supplier in Spain")
    assert (plan.tool, plan.decision.router) == ("research", "features")

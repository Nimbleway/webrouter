"""The classify contract: what it takes, what it returns, and what it refuses."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from web_router import (
    CLASSIFY_QUERY_MAX_CHARS,
    CLASSIFY_TIME_SECONDS_MAX,
    AsyncWebRouter,
    Budgets,
    MethodParams,
    Route,
    SearchParams,
    WebRouter,
)


@pytest.fixture
def router() -> WebRouter:
    return WebRouter()


def test_query_with_a_url_routes_to_extract(router: WebRouter) -> None:
    route = router.classify("Summarize https://example.com/pricing")

    assert route.method == "extract"
    assert route.method_params.chosen() == ["extract"]


def test_query_without_a_url_routes_to_search(router: WebRouter) -> None:
    route = router.classify("Who is the CEO of Example Corp?")

    assert route.method == "search"
    assert route.method_params.chosen() == ["search"]


def test_route_echoes_the_query(router: WebRouter) -> None:
    assert router.classify("pgvector vs qdrant").query == "pgvector vs qdrant"


def test_serialized_route_holds_only_the_chosen_methods_params(router: WebRouter) -> None:
    route = router.classify("Summarize https://example.com/pricing", Budgets(time_seconds=30, cost_usd=0.05))

    assert json.loads(route.model_dump_json()) == {
        "query": "Summarize https://example.com/pricing",
        "method": "extract",
        "method_params": {"extract": {}},
    }


def test_budgets_are_optional(router: WebRouter) -> None:
    assert router.classify("weather in Paris").method == "search"


def test_async_router_classifies_too() -> None:
    assert AsyncWebRouter().classify("weather in Paris").method == "search"


@pytest.mark.parametrize("query", ["", "x" * (CLASSIFY_QUERY_MAX_CHARS + 1)])
def test_query_outside_its_length_limits_is_refused(router: WebRouter, query: str) -> None:
    with pytest.raises(ValidationError):
        router.classify(query)


def test_query_at_the_length_limit_is_accepted(router: WebRouter) -> None:
    assert router.classify("x" * CLASSIFY_QUERY_MAX_CHARS).method == "search"


@pytest.mark.parametrize("seconds", [0, -1, CLASSIFY_TIME_SECONDS_MAX + 1])
def test_time_budget_outside_its_range_is_refused(seconds: int) -> None:
    with pytest.raises(ValidationError):
        Budgets(time_seconds=seconds)


@pytest.mark.parametrize("seconds", [1, CLASSIFY_TIME_SECONDS_MAX])
def test_time_budget_at_its_bounds_is_accepted(seconds: int) -> None:
    assert Budgets(time_seconds=seconds).time_seconds == seconds


@pytest.mark.parametrize("usd", [0, -0.01])
def test_cost_budget_that_is_not_positive_is_refused(usd: float) -> None:
    with pytest.raises(ValidationError):
        Budgets(cost_usd=usd)


def test_budgets_refuse_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Budgets.model_validate({"tokens": 100})


def test_route_refuses_params_for_another_method() -> None:
    with pytest.raises(ValidationError):
        Route(query="q", method="extract", method_params=MethodParams(search=SearchParams()))


def test_route_refuses_params_for_two_methods() -> None:
    with pytest.raises(ValidationError):
        Route.model_validate(
            {"query": "q", "method": "search", "method_params": {"search": {}, "extract": {}}},
        )


def test_route_refuses_an_unknown_method() -> None:
    with pytest.raises(ValidationError):
        Route.model_validate({"query": "q", "method": "crawl", "method_params": {"crawl": {}}})

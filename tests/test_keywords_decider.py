"""The keywords decider: deterministic, keyless, and on the normalized confidence scale, with no network."""

from __future__ import annotations

from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants_decisions import (
    KEYWORDS_CONFIDENCE,
    KEYWORDS_PAID_TOOL_CONFIDENCE,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_MIN_CONFIDENCE,
    ROUTE_QUESTIONS,
)
from web_router.decisions import KeywordsDecider, execution
from web_router.decisions.route import normalized_confidence


def router(decider: Any) -> WebRouter:
    return WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}}, decider=decider)


@pytest.mark.parametrize(
    ("query", "tool", "coverage", "well_scoped"),
    [
        ("Who is the CEO of Wikimedia?", "search", "lookup", "clear"),
        ("Summarize the page at https://example.com/pricing", "extract", "lookup", "clear"),
        ("Compare the Example Book 14 and the Sample Pro 13", "research", "synthesis", "clear"),
        ("opening hours", "search", "lookup", "missing_context"),
    ],
)
def test_the_keywords_decider_is_deterministic_and_keyless(
    query: str, tool: str, coverage: str, well_scoped: str
) -> None:
    answers = KeywordsDecider().decide({"query": query}, ROUTE_QUESTIONS)
    assert answers == KeywordsDecider().decide({"query": query}, ROUTE_QUESTIONS)
    assert (answers["tool"].choice, answers["coverage"].choice, answers["well_scoped"].choice) == (
        tool,
        coverage,
        well_scoped,
    )


@pytest.mark.parametrize(
    ("query", "recent"),
    [
        ("What is the BTC price today?", "day"),
        ("Is the API down now?", "day"),
        ("What did OpenAI announce this week?", "week"),
        ("Latest Python release", "month"),
    ],
)
def test_the_keywords_decider_maps_today_and_now_to_day_and_this_week_to_week(query: str, recent: str) -> None:
    assert execution.classify(WebRouter(decider="keywords"), query).labels["recent"].choice == recent


def test_the_keywords_decider_routes_a_url_to_extract_by_name() -> None:
    route = execution.classify(router("keywords"), "Summarize https://example.com/pricing")
    assert route.method == "extract" and route.labels["tool"].confidence >= ROUTE_MIN_CONFIDENCE
    # well_scoped's fixed 0.6 over two choices normalizes to 0.2: it takes its default ("clear") but moves no route.
    assert route.labels["well_scoped"].confidence == pytest.approx(0.2)
    assert route.fallback == ()


def test_the_keywords_decider_reports_its_fixed_confidences_on_the_normalized_scale() -> None:
    answers = KeywordsDecider().decide({"query": "Who is the CEO of Wikimedia?"}, ROUTE_QUESTIONS)
    for name, answer in answers.items():
        assert answer.probabilities[answer.choice] == pytest.approx(KEYWORDS_CONFIDENCE[name])
        assert sum(answer.probabilities.values()) == pytest.approx(1.0)
        assert answer.confidence == pytest.approx(normalized_confidence(answer.probabilities, answer.choice))


@pytest.mark.parametrize("query", ["list of US presidents"])
def test_a_keywords_research_pick_falls_back_to_search(query: str) -> None:
    route = execution.classify(router("keywords"), query)
    assert route.labels["tool"].choice == "research"
    assert route.labels["tool"].probabilities["research"] == pytest.approx(KEYWORDS_PAID_TOOL_CONFIDENCE)
    assert route.labels["tool"].confidence < ROUTE_MIN_CONFIDENCE
    assert route.method == "search" and ROUTE_FALLBACK_LOW_CONFIDENCE in route.fallback

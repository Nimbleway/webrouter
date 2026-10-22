"""Labels to a route and the text an agent reads: route.py on its own, with no router and no network."""

from __future__ import annotations

import pytest

from web_router.constants_decisions import (
    ROUTE_ADVICE_NO_URL,
    ROUTE_FALLBACK_NO_BACKEND,
    ROUTE_FALLBACK_NO_URL,
)
from web_router.decisions import Answer, Route, route_from_labels
from web_router.decisions.route import normalized_confidence, urls_in

CHAINS = {"search": (("tavily", {}),), "extract": (("jina", {}),), "research": (), "browse": ()}
SURE = 0.9


def answers(**choices: str) -> dict[str, Answer]:
    return {name: Answer(choice=choice, confidence=SURE) for name, choice in choices.items()}


def test_extract_with_no_url_searches_first() -> None:
    method, chain, reason = route_from_labels("read it", {"tool": "extract"}, CHAINS)
    assert (method, reason) == ("search", ROUTE_FALLBACK_NO_URL)
    assert chain == CHAINS["search"]


def test_a_method_with_no_backend_here_downgrades_to_search() -> None:
    method, _, reason = route_from_labels("report on x", {"tool": "research"}, CHAINS)
    assert (method, reason) == ("search", ROUTE_FALLBACK_NO_BACKEND)


def test_a_schema_routes_to_research_whatever_the_tool_label() -> None:
    chains = {**CHAINS, "research": (("nimble", {}),)}
    method, _, reason = route_from_labels("x", {"tool": "search"}, chains, output_schema={"type": "object"})
    assert (method, reason) == ("research", None)


def test_the_advice_never_names_two_tools_that_contradict() -> None:
    route = Route(
        query="q", method="search", chain=(), labels=answers(tool="extract"), fallback=(ROUTE_FALLBACK_NO_URL,)
    )
    assert route.advice == ROUTE_ADVICE_NO_URL


def test_the_text_has_one_line_per_label_in_a_fixed_order() -> None:
    labels = answers(tool="search", recent="day", well_scoped="missing_context", coverage="lookup")
    lines = str(Route(query="q", method="search", chain=(), labels=labels)).splitlines()
    assert [line.split(":")[0] for line in lines] == ["Recommended tool", "Freshness", "Request", "Evidence needed"]
    assert "ask the user" in lines[2]


def test_a_schema_with_no_research_backend_says_research_would_fit() -> None:
    route = Route(query="q", method="search", chain=(), fallback=(ROUTE_FALLBACK_NO_BACKEND,), requested="research")
    assert "research would fit" in route.advice


def test_an_unsure_label_reads_as_its_default_marked_unsure() -> None:
    labels = {**answers(tool="search"), "recent": Answer(choice="hour", confidence=0.2)}
    text = str(Route(query="q", method="search", chain=(), labels=labels))
    assert "past hour" not in text
    assert "any date is fine (unsure at 0.20, so the default)" in text


def test_a_url_ending_a_sentence_loses_its_punctuation() -> None:
    assert urls_in("Read https://example.com/page. Then https://example.org/a?b=1, please!") == [
        "https://example.com/page",
        "https://example.org/a?b=1",
    ]


def test_confidence_is_normalized_over_the_choices() -> None:
    four = {"search": 0.55, "extract": 0.15, "research": 0.15, "browse": 0.15}
    assert normalized_confidence(four, "search") == pytest.approx(0.4)
    assert normalized_confidence({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}, "a") == 0.0
    assert normalized_confidence({"a": 1.0, "b": 0.0}, "a") == 1.0
    assert normalized_confidence({"a": 0.1, "b": 0.9}, "a") == 0.0  # clamped, not negative
    assert normalized_confidence({"a": 0.7}, "a") == 0.7

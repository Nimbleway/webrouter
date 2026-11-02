"""The keywords decider: deterministic, keyless, and on the normalized confidence scale, with no network."""

from __future__ import annotations

from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants_decisions import (
    KEYWORDS_CONFIDENCE,
    ROUTE_QUESTIONS,
)
from web_router.decisions import KeywordsDecider
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


def test_the_keywords_decider_reports_its_fixed_confidences_on_the_normalized_scale() -> None:
    answers = KeywordsDecider().decide({"query": "Who is the CEO of Wikimedia?"}, ROUTE_QUESTIONS)
    for name, answer in answers.items():
        assert answer.probabilities[answer.choice] == pytest.approx(KEYWORDS_CONFIDENCE[name])
        assert sum(answer.probabilities.values()) == pytest.approx(1.0)
        assert answer.confidence == pytest.approx(normalized_confidence(answer.probabilities, answer.choice))

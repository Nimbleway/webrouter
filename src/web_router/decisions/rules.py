"""Simple keyword rules as a decider: keyless, offline and deterministic.

A floor to measure a model against, a decider for tests and CI, where no key
exists, and a safe keyless default. It is not a model and says nothing about
how one would label a query. Its confidences are fixed, not measured: one
probability per label, reported on Jev's normalized scale, and a lower one for
a research or browse pick, below ROUTE_MIN_CONFIDENCE, so a paid method is
never picked on keywords alone and the route falls back to search unless the
caller overrides it.
"""

from __future__ import annotations

import re
from typing import Any

from web_router.constants_decisions import (
    KEYWORDS_CONFIDENCE,
    KEYWORDS_MISSING_CONTEXT_WORDS,
    KEYWORDS_PAID_TOOL_CONFIDENCE,
    KEYWORDS_PAID_TOOLS,
    KEYWORDS_RECENT_MONTH,
    KEYWORDS_RESEARCH,
    KEYWORDS_RESEARCH_LENGTH,
    KEYWORDS_SYNTHESIS,
    KEYWORDS_URL,
    KEYWORDS_VERSION,
    ROUTE_DECIDER_KEYWORDS,
)
from web_router.decisions.route import Answer, normalized_confidence

_URL = re.compile(KEYWORDS_URL)
_RESEARCH = re.compile(KEYWORDS_RESEARCH)
_RECENT_MONTH = re.compile(KEYWORDS_RECENT_MONTH)
_SYNTHESIS = re.compile(KEYWORDS_SYNTHESIS)


class KeywordsDecider:
    """Answers ROUTE_QUESTIONS from keywords and length alone, with a fixed (not measured) confidence per label."""

    name = ROUTE_DECIDER_KEYWORDS
    version = KEYWORDS_VERSION

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """Label ``state["query"]``; only the questions asked that the rules know are answered."""
        query = str(state["query"]).lower()
        picks = {
            "tool": _tool(query),
            "recent": "month" if _RECENT_MONTH.search(query) else "any",
            "coverage": _coverage(query),
            "well_scoped": "missing_context" if len(query.split()) <= KEYWORDS_MISSING_CONTEXT_WORDS else "clear",
        }
        return {
            name: _answer(choice, list(questions[name]["criteria"]), _probability(name, choice))
            for name, choice in picks.items()
            if name in questions
        }

    def close(self) -> None:
        """Nothing to release."""


def _tool(query: str) -> str:
    if _URL.search(query):
        return "extract"
    return "research" if _RESEARCH.search(query) else "search"


def _coverage(query: str) -> str:
    if len(query) > KEYWORDS_RESEARCH_LENGTH:
        return "research"
    return "synthesis" if _SYNTHESIS.search(query) else "lookup"


def _probability(name: str, choice: str) -> float:
    if name == "tool" and choice in KEYWORDS_PAID_TOOLS:
        return KEYWORDS_PAID_TOOL_CONFIDENCE
    return KEYWORDS_CONFIDENCE[name]


def _answer(choice: str, choices: list[str], probability: float) -> Answer:
    others = [c for c in choices if c != choice]
    rest = (1.0 - probability) / len(others) if others else 0.0
    probabilities = {c: probability if c == choice else rest for c in choices}
    return Answer(choice=choice, confidence=normalized_confidence(probabilities, choice), probabilities=probabilities)

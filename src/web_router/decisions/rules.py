"""Keyword rules as a decider: keyless, offline and deterministic.

A floor to measure a model against, and a decider for tests and CI, where no
key exists. It is not a model and says nothing about how one would label a
query. The patterns are Bench's stand-in rules, kept the same so the two runs
compare.
"""

from __future__ import annotations

import re
from typing import Any

from web_router.constants import (
    ROUTE_DECIDER_RULES,
    RULES_CONFIDENCE,
    RULES_MISSING_CONTEXT_WORDS,
    RULES_RECENT_MONTH,
    RULES_RESEARCH,
    RULES_RESEARCH_LENGTH,
    RULES_SYNTHESIS,
    RULES_URL,
    RULES_VERSION,
)
from web_router.decisions.route import Answer

_URL = re.compile(RULES_URL)
_RESEARCH = re.compile(RULES_RESEARCH)
_RECENT_MONTH = re.compile(RULES_RECENT_MONTH)
_SYNTHESIS = re.compile(RULES_SYNTHESIS)


class RulesDecider:
    """Answers ROUTE_QUESTIONS from keywords and length alone, with a fixed confidence per label."""

    name = ROUTE_DECIDER_RULES
    version = RULES_VERSION

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """Label ``state["query"]``; only the questions asked that the rules know are answered."""
        query = str(state["query"]).lower()
        picks = {
            "tool": _tool(query),
            "recent": "month" if _RECENT_MONTH.search(query) else "any",
            "coverage": _coverage(query),
            "well_scoped": "missing_context" if len(query.split()) <= RULES_MISSING_CONTEXT_WORDS else "clear",
        }
        return {
            name: _answer(choice, list(questions[name]["criteria"]), RULES_CONFIDENCE[name])
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
    if len(query) > RULES_RESEARCH_LENGTH:
        return "research"
    return "synthesis" if _SYNTHESIS.search(query) else "lookup"


def _answer(choice: str, choices: list[str], confidence: float) -> Answer:
    others = [c for c in choices if c != choice]
    rest = (1.0 - confidence) / len(others) if others else 0.0
    probabilities = {c: confidence if c == choice else rest for c in choices}
    return Answer(choice=choice, confidence=confidence, probabilities=probabilities)

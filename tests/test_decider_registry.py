"""The decider slot: the registry, the fallbacks and passthrough, with no network."""

from __future__ import annotations

from typing import Any

import pytest

from web_router import WebRouter
from web_router.decisions import (
    Answer,
    KeywordsDecider,
    available_deciders,
    create_decider,
    execution,
    register_decider,
    unregister_decider,
)
from web_router.exceptions import ConfigurationError

SURE = 0.9


def sure_answers(**choices: str) -> dict[str, Answer]:
    base = {"tool": "search", "recent": "any", "coverage": "lookup", "well_scoped": "clear", **choices}
    return {name: Answer(choice=choice, confidence=SURE) for name, choice in base.items()}


class FakeDecider:
    """Answers with what it was given, or raises what it was given; notes whether it was closed."""

    name = "fake"
    version = "fake-1"

    def __init__(self, answers: Any = None, error: BaseException | None = None) -> None:
        self.answers = answers if answers is not None else sure_answers()
        self.error = error
        self.closed = False

    def decide(self, state: Any, questions: dict[str, Any]) -> Any:
        if self.error is not None:
            raise self.error
        return self.answers

    def close(self) -> None:
        self.closed = True


def router(decider: Any) -> WebRouter:
    return WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}}, decider=decider)


def test_a_registered_name_overrides_a_builtin_of_the_same_name() -> None:
    register_decider("keywords", FakeDecider)
    try:
        assert isinstance(create_decider("keywords"), FakeDecider)
    finally:
        unregister_decider("keywords")
    assert isinstance(create_decider("keywords"), KeywordsDecider)


def test_the_builtin_deciders_are_listed_by_their_new_names() -> None:
    assert {"jev", "llm", "keywords"} <= set(available_deciders())
    assert "openai" not in available_deciders() and "rules" not in available_deciders()


def test_the_no_decider_error_names_the_keyless_decider_first() -> None:
    with pytest.raises(ConfigurationError) as caught:
        execution.classify(WebRouter(), "who won")
    message = str(caught.value)
    assert message.index("keywords") < message.index("jev")


class GuardError(PermissionError):
    """What a caller's data policy raises before any call leaves the machine."""

"""The decider slot: the registry, the fallbacks and passthrough, with no network."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants_decisions import ROUTE_FALLBACK_DECIDER_ERROR
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


def test_an_unknown_decider_name_is_refused_and_the_known_ones_listed() -> None:
    with pytest.raises(ConfigurationError, match="'keywords'"):
        WebRouter(decider="nonexistent")


def test_a_registered_decider_works_by_name_and_is_closed_by_the_router() -> None:
    built: list[FakeDecider] = []

    def factory() -> FakeDecider:
        built.append(FakeDecider())
        return built[-1]

    register_decider("acme", factory)
    try:
        assert "acme" in available_deciders()
        acme = router("acme")
        assert acme.decider is built[0]
        acme.close()
        assert built[0].closed
    finally:
        unregister_decider("acme")
    assert "acme" not in available_deciders()


def test_a_registered_name_overrides_a_builtin_of_the_same_name() -> None:
    register_decider("keywords", FakeDecider)
    try:
        assert isinstance(create_decider("keywords"), FakeDecider)
    finally:
        unregister_decider("keywords")
    assert isinstance(create_decider("keywords"), KeywordsDecider)


def test_an_instance_is_used_as_given_and_left_open() -> None:
    decider = FakeDecider()
    wr = router(decider)
    assert wr.decider is decider
    wr.close()
    assert not decider.closed


def test_a_bare_import_of_the_decider_slot_needs_neither_httpx_nor_openai() -> None:
    code = (
        "import sys; sys.modules['httpx'] = None; sys.modules['openai'] = None\n"
        "from web_router import WebRouter\n"
        "from web_router.decisions import DecisionProvider, available_deciders\n"
        "assert 'keywords' in available_deciders()\n"
        "WebRouter(decider='keywords').classify('who won')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_a_decider_raising_anything_takes_the_decider_error_fallback() -> None:
    """A third-party decider's TimeoutError, not a ProviderError, still gives the default route."""
    route = execution.classify(router(FakeDecider(error=TimeoutError("slow"))), "who won")
    assert route.method == "search" and route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)
    assert route.labels == {}


@pytest.mark.parametrize("answers", [{"tool": "search"}])
def test_a_malformed_decider_result_takes_the_decider_error_fallback(answers: Any) -> None:
    route = execution.classify(router(FakeDecider(answers)), "who won")
    assert route.method == "search" and route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


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


def test_a_deciders_passthrough_types_stop_the_run_instead_of_becoming_a_default_route() -> None:
    decider = FakeDecider(error=GuardError("private data"))
    decider.passthrough = (PermissionError,)  # type: ignore[attr-defined]
    with pytest.raises(GuardError):
        execution.classify(router(decider), "who won")
    assert execution.classify(router(FakeDecider(error=GuardError("private data"))), "who won").fallback == (
        ROUTE_FALLBACK_DECIDER_ERROR,
    )


@pytest.mark.parametrize("passthrough", [TimeoutError, (TimeoutError,)])
def test_a_single_passthrough_class_works_like_a_one_item_tuple(passthrough: Any) -> None:
    decider = FakeDecider()
    decider.passthrough = passthrough  # type: ignore[attr-defined]
    assert execution.classify(router(decider), "who won").method == "search"
    decider.error = TimeoutError("caller's deadline")
    with pytest.raises(TimeoutError):
        execution.classify(router(decider), "who won")


def test_a_passthrough_that_is_not_an_exception_class_raises_when_the_router_is_built() -> None:
    decider = FakeDecider()
    decider.passthrough = "TimeoutError"  # type: ignore[attr-defined]
    with pytest.raises(ConfigurationError, match="passthrough"):
        router(decider)

"""The decider slot: the registry, Jev and the keywords decider, with no network."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from web_router import WebRouter
from web_router.constants_decisions import (
    KEYWORDS_CONFIDENCE,
    KEYWORDS_PAID_TOOL_CONFIDENCE,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_MIN_CONFIDENCE,
    ROUTE_QUESTIONS,
)
from web_router.decisions import (
    Answer,
    JevDecider,
    KeywordsDecider,
    available_deciders,
    create_decider,
    register_decider,
    unregister_decider,
)
from web_router.decisions.route import normalized_confidence
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError

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


def test_jev_by_name_is_still_built_owned_and_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        WebRouter(decider="jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = WebRouter(decider="jev")
    assert isinstance(jev.decider, JevDecider)
    jev.close()
    assert jev.decider._client.is_closed


def test_an_instance_is_used_as_given_and_left_open() -> None:
    decider = FakeDecider()
    wr = router(decider)
    assert wr.decider is decider
    wr.close()
    assert not decider.closed


def test_a_missing_dependency_is_reported_as_an_extra_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "web_router.decisions.jev", raising=False)
    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    with pytest.raises(ProviderNotInstalledError, match="search"):
        create_decider("jev")


def jev_with_reply(
    payload: Any, monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response] | None = None
) -> JevDecider:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = JevDecider()
    jev._client = httpx.Client(
        base_url="https://jev.test",
        transport=httpx.MockTransport(handler or (lambda request: httpx.Response(200, json=payload))),
    )
    return jev


def test_jev_decider_reads_a_reply_and_wraps_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    probabilities = {"search": 0.1, "extract": 0.8, "research": 0.05, "browse": 0.05}
    reply = {"answers": {"tool": {"choice": "extract", "confidence": 0.7, "probabilities": probabilities}}}
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    answers = jev_with_reply(None, monkeypatch, answer).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert answers == {"tool": Answer(choice="extract", confidence=0.7, probabilities=probabilities)}
    assert sent[0]["questions"] == ROUTE_QUESTIONS

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    with pytest.raises(ProviderError, match="HTTP 500: down") as caught:
        jev_with_reply(None, monkeypatch, fail).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, httpx.HTTPStatusError)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError) as caught:
        jev_with_reply(None, monkeypatch, slow).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, httpx.ReadTimeout)


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


def test_the_keywords_decider_routes_a_url_to_extract_by_name() -> None:
    route = router("keywords").classify("Summarize https://example.com/pricing")
    assert route.method == "extract" and route.labels["tool"].confidence >= ROUTE_MIN_CONFIDENCE
    # well_scoped's fixed 0.6 over two choices normalizes to 0.2: it takes its default ("clear") but moves no route.
    assert route.labels["well_scoped"].confidence == pytest.approx(0.2)
    assert route.fallback == ()


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


def test_confidence_is_normalized_over_the_choices() -> None:
    four = {"search": 0.55, "extract": 0.15, "research": 0.15, "browse": 0.15}
    assert normalized_confidence(four, "search") == pytest.approx(0.4)
    assert normalized_confidence({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}, "a") == 0.0
    assert normalized_confidence({"a": 1.0, "b": 0.0}, "a") == 1.0
    assert normalized_confidence({"a": 0.1, "b": 0.9}, "a") == 0.0  # clamped, not negative
    assert normalized_confidence({"a": 0.7}, "a") == 0.7


def test_a_decider_raising_anything_takes_the_decider_error_fallback() -> None:
    """A third-party decider's TimeoutError, not a ProviderError, still gives the default route."""
    route = router(FakeDecider(error=TimeoutError("slow"))).classify("who won")
    assert route.method == "search" and route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)
    assert route.labels == {}


@pytest.mark.parametrize("answers", [{"tool": "search"}])
def test_a_malformed_decider_result_takes_the_decider_error_fallback(answers: Any) -> None:
    route = router(FakeDecider(answers)).classify("who won")
    assert route.method == "search" and route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_the_keywords_decider_reports_its_fixed_confidences_on_the_normalized_scale() -> None:
    answers = KeywordsDecider().decide({"query": "Who is the CEO of Wikimedia?"}, ROUTE_QUESTIONS)
    for name, answer in answers.items():
        assert answer.probabilities[answer.choice] == pytest.approx(KEYWORDS_CONFIDENCE[name])
        assert sum(answer.probabilities.values()) == pytest.approx(1.0)
        assert answer.confidence == pytest.approx(normalized_confidence(answer.probabilities, answer.choice))


@pytest.mark.parametrize("query", ["list of US presidents"])
def test_a_keywords_research_pick_falls_back_to_search(query: str) -> None:
    route = router("keywords").classify(query)
    assert route.labels["tool"].choice == "research"
    assert route.labels["tool"].probabilities["research"] == pytest.approx(KEYWORDS_PAID_TOOL_CONFIDENCE)
    assert route.labels["tool"].confidence < ROUTE_MIN_CONFIDENCE
    assert route.method == "search" and ROUTE_FALLBACK_LOW_CONFIDENCE in route.fallback


def test_the_builtin_deciders_are_listed_by_their_new_names() -> None:
    assert {"jev", "llm", "keywords"} <= set(available_deciders())
    assert "openai" not in available_deciders() and "rules" not in available_deciders()


def test_the_no_decider_error_names_the_keyless_decider_first() -> None:
    with pytest.raises(ConfigurationError) as caught:
        WebRouter().classify("who won")
    message = str(caught.value)
    assert message.index("keywords") < message.index("jev")


class GuardError(PermissionError):
    """What a caller's data policy raises before any call leaves the machine."""


def test_a_deciders_passthrough_types_stop_the_run_instead_of_becoming_a_default_route() -> None:
    decider = FakeDecider(error=GuardError("private data"))
    decider.passthrough = (PermissionError,)  # type: ignore[attr-defined]
    with pytest.raises(GuardError):
        router(decider).classify("who won")
    assert router(FakeDecider(error=GuardError("private data"))).classify("who won").fallback == (
        ROUTE_FALLBACK_DECIDER_ERROR,
    )

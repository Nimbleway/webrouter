"""The decision gate: labels to a route, the text an agent reads, and WebRouter.classify(), with no network."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import httpx
import pytest

from web_router import WebRouter
from web_router.constants import (
    ROUTE_ADVICE_NO_URL,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_FALLBACK_NO_BACKEND,
    ROUTE_FALLBACK_NO_URL,
)
from web_router.decisions import Answer, JevDecider, Route, route_from_labels
from web_router.decisions.route import label_values, urls_in
from web_router.exceptions import ConfigurationError, ProviderError

CHAINS = {"search": (("tavily", {}),), "extract": (("jina", {}),), "research": (), "browse": ()}
SURE = 0.9


def answers(**choices: str) -> dict[str, Answer]:
    return {name: Answer(choice=choice, confidence=SURE) for name, choice in choices.items()}


class FakeDecider:
    """Answers every question with fixed labels, or fails; records what it was asked and whether it was closed."""

    def __init__(self, labels: dict[str, Answer] | None = None, fail: bool = False) -> None:
        self.labels = labels or {}
        self.fail = fail
        self.calls = 0
        self.closed = False

    def decide(self, state: Any, questions: dict[str, Any]) -> dict[str, Answer]:
        self.calls += 1
        if self.fail:
            raise ProviderError("fake", "down")
        return self.labels

    def close(self) -> None:
        self.closed = True


def test_an_unsure_or_missing_label_takes_its_default() -> None:
    values, unsure = label_values({"tool": Answer(choice="research", confidence=0.2)})
    assert values["tool"] == "search"
    assert values["recent"] == "any"
    assert unsure


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


def test_low_confidence_advice_says_where_the_classifier_leaned() -> None:
    labels = {"tool": Answer(choice="browse", confidence=0.3)}
    route = Route(query="q", method="search", chain=(), labels=labels, fallback=(ROUTE_FALLBACK_LOW_CONFIDENCE,))
    assert route.advice == "search, low confidence (the classifier leaned browse)"


def test_the_text_has_one_line_per_label_in_a_fixed_order() -> None:
    labels = answers(tool="search", recent="day", well_scoped="missing_context", coverage="lookup")
    lines = str(Route(query="q", method="search", chain=(), labels=labels)).splitlines()
    assert [line.split(":")[0] for line in lines] == ["Recommended tool", "Freshness", "Request", "Evidence needed"]
    assert "ask the user" in lines[2]


def test_classify_needs_a_decider() -> None:
    with pytest.raises(ConfigurationError):
        WebRouter().classify("anything")


def test_a_given_url_routes_to_extract_without_asking() -> None:
    decider = FakeDecider()
    route = WebRouter(decider=decider).classify("summarize this", urls="https://example.com")
    assert route.method == "extract"
    assert route.urls == ["https://example.com"]
    assert decider.calls == 0


def test_a_failing_decider_gives_the_default_route_and_says_why() -> None:
    route = WebRouter(decider=FakeDecider(fail=True)).classify("who won")
    assert route.method == "search"
    assert ROUTE_FALLBACK_DECIDER_ERROR in route.fallback
    assert "classifier was unavailable" in route.advice


def test_the_route_follows_a_configured_auto_chain() -> None:
    router = WebRouter(
        provider_config={"auto": {"chain": {"search": ["tavily"]}}}, decider=FakeDecider(answers(tool="search"))
    )
    assert router.classify("who won").providers == ["tavily"]


def test_close_leaves_a_decider_it_was_handed_open() -> None:
    decider = FakeDecider()
    WebRouter(decider=decider).close()
    assert not decider.closed


def test_close_closes_a_decider_it_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    router = WebRouter(decider="jev")
    assert isinstance(router.decider, JevDecider)
    router.close()
    assert router.decider._client.is_closed


def test_a_reply_of_the_wrong_shape_is_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A list where answers should be a mapping raised AttributeError past classify()'s fallback."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    decider = JevDecider()
    decider._client = httpx.Client(
        base_url="https://jev.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"answers": []})),
    )
    with pytest.raises(ProviderError):
        decider.decide({"query": "q"}, {})
    route = WebRouter(decider=decider).classify("who won")
    assert ROUTE_FALLBACK_DECIDER_ERROR in route.fallback


def test_a_schema_with_no_research_backend_says_research_would_fit() -> None:
    route = Route(query="q", method="search", chain=(), fallback=(ROUTE_FALLBACK_NO_BACKEND,), requested="research")
    assert "research would fit" in route.advice


def test_an_unsure_label_reads_as_its_default_marked_unsure() -> None:
    labels = {**answers(tool="search"), "recent": Answer(choice="hour", confidence=0.2)}
    text = str(Route(query="q", method="search", chain=(), labels=labels))
    assert "past hour" not in text
    assert "any date is fine (unsure at 0.20, so the default)" in text


def test_an_override_wins_over_the_routes_own_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    router = WebRouter(decider=FakeDecider(answers(tool="search")))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        router, "search", lambda **kwargs: seen.update({k: v for k, v in kwargs.items() if k != "query"})
    )
    router.execute(router.classify("who won"), provider="tavily", max_results=3)
    assert seen == {"provider": "tavily", "max_results": 3}


def test_a_bare_import_does_not_need_httpx() -> None:
    """The base install has no httpx: only WebRouter(decider="jev") may load the Jev client."""
    code = (
        "import sys; sys.modules['httpx'] = None\n"
        "from web_router import WebRouter\n"
        "from web_router.decisions import Route\n"
        "WebRouter()\n"
    )
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def test_routes_with_other_member_options_get_their_own_provider() -> None:
    router = WebRouter(decider=FakeDecider())
    plain = Route(query="q", method="search", chain=(("tavily", {}),))
    tuned = Route(query="q", method="search", chain=(("tavily", {"allow_keyless": True}),))
    assert router._routed_provider(plain) is not router._routed_provider(tuned)
    assert router._routed_provider(plain) is router._routed_provider(plain)


def test_an_unsure_tool_label_says_where_the_classifier_leaned() -> None:
    route = WebRouter(decider=FakeDecider({"tool": Answer(choice="browse", confidence=0.3)})).classify("who won")
    assert route.method == "search"
    assert route.advice == "search, low confidence (the classifier leaned browse)"


def test_a_missing_tool_label_is_flagged_as_low_confidence() -> None:
    route = WebRouter(decider=FakeDecider(answers(recent="day"))).classify("who won")
    assert route.advice == "search, low confidence"


def test_a_url_ending_a_sentence_loses_its_punctuation() -> None:
    assert urls_in("Read https://example.com/page. Then https://example.org/a?b=1, please!") == [
        "https://example.com/page",
        "https://example.org/a?b=1",
    ]


def test_an_override_can_replace_the_routes_query(monkeypatch: pytest.MonkeyPatch) -> None:
    router = WebRouter(decider=FakeDecider(answers(tool="search")))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(router, "search", lambda **kwargs: seen.update(kwargs))
    router.execute(router.classify("who won"), query="who won in 2024")
    assert seen["query"] == "who won in 2024"


def test_a_provider_override_builds_no_routed_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    router = WebRouter(decider=FakeDecider(answers(tool="search")))
    monkeypatch.setattr(router, "search", lambda **kwargs: None)
    monkeypatch.setattr(router, "_routed_provider", lambda route: pytest.fail("built a provider it will not use"))
    router.execute(router.classify("who won"), provider="tavily")


def test_a_tool_label_below_the_threshold_still_records_low_confidence() -> None:
    labels = {**answers(recent="any", coverage="lookup", well_scoped="clear"), "tool": Answer("search", 0.3)}
    route = WebRouter(decider=FakeDecider(labels)).classify("who won")
    assert route.fallback == (ROUTE_FALLBACK_LOW_CONFIDENCE,)


def test_settings_with_a_ready_made_decider_raise() -> None:
    with pytest.raises(ConfigurationError):
        WebRouter(decider=FakeDecider(), decider_config={"api_key": "x"})
    with pytest.raises(ConfigurationError):
        WebRouter(decider_config={"api_key": "x"})


def test_settings_with_a_decider_name_still_build() -> None:
    pytest.importorskip("openai")
    router = WebRouter(decider="llm", decider_config={"api_key": "x"})
    assert router.decider.name == "llm"
    router.close()

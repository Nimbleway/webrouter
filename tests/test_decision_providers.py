"""The decider slot: the registry and the three built-in deciders, with no network."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from typing import Any

import httpx
import pytest

from web_router import WebRouter
from web_router.constants import (
    KEYWORDS_CONFIDENCE,
    KEYWORDS_PAID_TOOL_CONFIDENCE,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_MIN_CONFIDENCE,
    ROUTE_QUESTIONS,
)
from web_router.decisions import (
    Answer,
    DecisionProvider,
    JevDecider,
    KeywordsDecider,
    LLMDecider,
    available_deciders,
    create_decider,
    register_decider,
    unregister_decider,
)
from web_router.decisions.llm import prompt_digest
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


def test_the_builtin_deciders_resolve_by_name() -> None:
    assert {"jev", "llm", "keywords"} <= set(available_deciders())
    assert isinstance(create_decider("keywords"), KeywordsDecider)


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


def jev_with_reply(payload: Any, monkeypatch: pytest.MonkeyPatch) -> JevDecider:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    jev = JevDecider()
    jev._client = httpx.Client(
        base_url="https://jev.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    return jev


def fake_openai(content: str) -> tuple[types.ModuleType, dict[str, Any]]:
    """The SDK's stand-in: records the client's options and every request, and answers ``content``."""
    seen: dict[str, Any] = {"built": [], "requests": [], "closed": 0}

    class Completions:
        def create(self, **request: Any) -> Any:
            seen["requests"].append(request)
            message = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            seen["built"].append(kwargs)
            self.chat = types.SimpleNamespace(completions=Completions())

        def close(self) -> None:
            seen["closed"] += 1

    module = types.ModuleType("openai")
    module.OpenAI = Client  # type: ignore[attr-defined]
    return module, seen


def model_reply(**choices: str) -> str:
    reply = {}
    for name, spec in ROUTE_QUESTIONS.items():
        options = list(spec["criteria"])
        pick = choices.get(name, options[0])
        reply[name] = {
            "choice": pick,
            "probabilities": {o: (0.85 if o == pick else 0.15 / (len(options) - 1)) for o in options},
        }
    return json.dumps(reply)


def test_the_llm_decider_asks_for_structured_labels_and_reads_them(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = fake_openai(model_reply(recent="week"))
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    decider = LLMDecider()
    route = router(decider).classify("this week's OpenAI announcements")
    n = len(ROUTE_QUESTIONS["recent"]["criteria"])
    assert route.labels["recent"].choice == "week"
    assert route.labels["recent"].confidence == pytest.approx((0.85 - 1 / n) / (1 - 1 / n))
    assert route.fallback == ()
    request = seen["requests"][0]
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["tool"]["properties"]["choice"]["enum"] == list(
        ROUTE_QUESTIONS["tool"]["criteria"]
    )
    assert "this week's OpenAI announcements" in request["messages"][0]["content"]
    assert seen["built"][0]["api_key"] == "test-key"
    assert re.fullmatch(r"gpt-4\.1-mini\+p[0-9a-f]{8}", decider.version)
    assert decider.timeout_s == 5.0
    decider.close()
    assert seen["closed"] == 1


def test_the_llm_decider_reads_its_key_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fake_openai(model_reply())
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        WebRouter(decider="llm")


def test_the_llm_decider_takes_its_key_and_endpoint_through_decider_config(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = fake_openai(model_reply())
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = {"model": "local-model", "base_url": "http://localhost:8000/v1", "api_key": " k-1 "}
    wr = WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}}, decider="llm", decider_config=config)
    assert isinstance(wr.decider, LLMDecider) and wr.decider.model == "local-model"
    assert seen["built"][0]["api_key"] == "k-1" and seen["built"][0]["base_url"] == "http://localhost:8000/v1"
    wr.classify("who won")
    assert seen["requests"][0]["model"] == "local-model"


def test_the_llm_decider_without_its_sdk_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(ProviderNotInstalledError, match="evals"):
        LLMDecider()


def test_a_client_handed_to_the_llm_decider_needs_no_key_and_stays_open(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = fake_openai(model_reply())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    decider = LLMDecider("gpt-4.1", client=module.OpenAI())
    decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    decider.close()
    assert seen["requests"][0]["model"] == "gpt-4.1" and seen["closed"] == 0


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"tool": {"choice": "search"}}),
        json.dumps([1]),
        json.dumps({k: v for k, v in json.loads(model_reply()).items() if k != "well_scoped"}),
        json.dumps({**json.loads(model_reply()), "tool": {"choice": "crawl", "probabilities": {"crawl": 1.0}}}),
    ],
)
def test_an_unreadable_llm_reply_is_a_provider_error(content: str, monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fake_openai(content)
    decider = LLMDecider(client=module.OpenAI())
    with pytest.raises(ProviderError):
        decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    assert router(decider).classify("who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_an_llm_sdk_failure_is_a_provider_error() -> None:
    def fail(**request: Any) -> Any:
        raise TimeoutError("read timed out")

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fail)))
    with pytest.raises(ProviderError) as caught:
        LLMDecider(client=client).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, TimeoutError)


@pytest.mark.parametrize(
    ("query", "tool", "coverage", "well_scoped"),
    [
        ("Who is the CEO of Wikimedia?", "search", "lookup", "clear"),
        ("Summarize the page at https://example.com/pricing", "extract", "lookup", "clear"),
        ("Compare the MacBook Air and the Dell XPS 13", "research", "synthesis", "clear"),
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


def test_the_llm_version_changes_with_the_prompt_or_the_schema() -> None:
    changed = {**ROUTE_QUESTIONS, "tool": {**ROUTE_QUESTIONS["tool"], "instructions": "Pick a tool."}}
    assert re.fullmatch(r"[0-9a-f]{8}", prompt_digest())
    assert prompt_digest(changed) != prompt_digest()
    assert prompt_digest() == prompt_digest(dict(ROUTE_QUESTIONS))


def test_confidence_is_normalized_over_the_choices() -> None:
    four = {"search": 0.55, "extract": 0.15, "research": 0.15, "browse": 0.15}
    assert normalized_confidence(four, "search") == pytest.approx(0.4)
    assert normalized_confidence({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}, "a") == 0.0
    assert normalized_confidence({"a": 1.0, "b": 0.0}, "a") == 1.0
    assert normalized_confidence({"a": 0.1, "b": 0.9}, "a") == 0.0  # clamped, not negative
    assert normalized_confidence({"a": 0.7}, "a") == 0.7


def test_an_llm_pick_at_p_055_over_four_tools_falls_back() -> None:
    """The same raw probability that would clear 0.5 is about 0.40 once normalized, so the route takes its default."""
    reply = json.loads(model_reply(tool="research"))
    reply["tool"]["probabilities"] = {"search": 0.15, "extract": 0.15, "research": 0.55, "browse": 0.15}
    module, _ = fake_openai(json.dumps(reply))
    route = router(LLMDecider(client=module.OpenAI())).classify("who won")
    assert route.labels["tool"].confidence == pytest.approx(0.4)
    assert route.method == "search" and ROUTE_FALLBACK_LOW_CONFIDENCE in route.fallback


def test_llm_probabilities_are_normalized_before_the_sum_check() -> None:
    """A confident answer whose probabilities drift from 1 is scaled, not rejected as a decider error."""
    reply = json.loads(model_reply())
    reply["tool"]["probabilities"] = {"search": 0.9, "extract": 0.2, "research": 0.1, "browse": 0.0}
    module, _ = fake_openai(json.dumps(reply))
    route = router(LLMDecider(client=module.OpenAI())).classify("who won")
    assert route.fallback == ()
    assert sum(route.labels["tool"].probabilities.values()) == pytest.approx(1.0)
    assert route.labels["tool"].probabilities["search"] == pytest.approx(0.75)


def test_a_decider_raising_anything_takes_the_decider_error_fallback() -> None:
    """A third-party decider's TimeoutError, not a ProviderError, still gives the default route."""
    route = router(FakeDecider(error=TimeoutError("slow"))).classify("who won")
    assert route.method == "search" and route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)
    assert route.labels == {}


@pytest.mark.parametrize("answers", [[1], {"tool": "search"}])
def test_a_malformed_decider_result_takes_the_decider_error_fallback(answers: Any) -> None:
    route = router(FakeDecider(answers)).classify("who won")
    assert route.method == "search" and route.fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_the_keywords_decider_reports_its_fixed_confidences_on_the_normalized_scale() -> None:
    answers = KeywordsDecider().decide({"query": "Who is the CEO of Wikimedia?"}, ROUTE_QUESTIONS)
    for name, answer in answers.items():
        assert answer.probabilities[answer.choice] == pytest.approx(KEYWORDS_CONFIDENCE[name])
        assert sum(answer.probabilities.values()) == pytest.approx(1.0)
        assert answer.confidence == pytest.approx(normalized_confidence(answer.probabilities, answer.choice))


@pytest.mark.parametrize("query", ["list of US presidents", "Compare the MacBook Air and the Dell XPS 13"])
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


@pytest.mark.parametrize(
    "probabilities",
    [
        {"search": 0.0, "extract": 0.0},
        {"search": 1.2, "extract": -0.2},
        {"search": float("nan")},
        {"search": 1e308, "extract": 1e308},
    ],
)
def test_an_llm_distribution_that_cannot_be_scaled_is_a_decider_error(probabilities: dict[str, float]) -> None:
    reply = json.loads(model_reply())
    reply["tool"]["probabilities"] = {**dict.fromkeys(ROUTE_QUESTIONS["tool"]["criteria"], 0.0), **probabilities}
    module, _ = fake_openai(json.dumps(reply))
    decider = LLMDecider(client=module.OpenAI())
    with pytest.raises(ProviderError):
        decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    assert router(decider).classify("who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


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


def test_every_builtin_decider_is_a_decision_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fake_openai(model_reply())
    for provider in (KeywordsDecider(), LLMDecider(client=module.OpenAI()), jev_with_reply({}, monkeypatch)):
        assert isinstance(provider, DecisionProvider), provider.name

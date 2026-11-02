"""The llm decider: structured labels from a chat model, with no network."""

from __future__ import annotations

import json
import re
import sys
import types
from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants import EVALS_FIXED_TEMPERATURE_MODELS
from web_router.constants_decisions import (
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_QUESTIONS,
)
from web_router.decisions import LLMDecider, execution
from web_router.decisions.llm_prompt import prompt_digest
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError


def router(decider: Any) -> WebRouter:
    return WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}}, decider=decider)


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
    route = execution.classify(router(decider), "this week's OpenAI announcements")
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
    decider.close()
    assert seen["closed"] == 1


def test_the_llm_decider_sends_no_temperature_to_a_fixed_temperature_model() -> None:
    module, seen = fake_openai(model_reply())
    model = f"{EVALS_FIXED_TEMPERATURE_MODELS[0]}-mini"
    LLMDecider(model, client=module.OpenAI()).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert "temperature" not in seen["requests"][0]


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
    execution.classify(wr, "who won")
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
        json.dumps({k: v for k, v in json.loads(model_reply()).items() if k != "well_scoped"}),
        json.dumps({**json.loads(model_reply()), "tool": {"choice": "crawl", "probabilities": {"crawl": 1.0}}}),
    ],
)
def test_an_unreadable_llm_reply_is_a_provider_error(content: str, monkeypatch: pytest.MonkeyPatch) -> None:
    module, _ = fake_openai(content)
    decider = LLMDecider(client=module.OpenAI())
    with pytest.raises(ProviderError):
        decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    assert execution.classify(router(decider), "who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)


def test_an_llm_sdk_failure_is_a_provider_error() -> None:
    def fail(**request: Any) -> Any:
        raise TimeoutError("read timed out")

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fail)))
    with pytest.raises(ProviderError) as caught:
        LLMDecider(client=client).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert isinstance(caught.value.original, TimeoutError)


def test_the_llm_version_changes_with_the_prompt_or_the_schema() -> None:
    changed = {**ROUTE_QUESTIONS, "tool": {**ROUTE_QUESTIONS["tool"], "instructions": "Pick a tool."}}
    assert re.fullmatch(r"[0-9a-f]{8}", prompt_digest())
    assert prompt_digest(changed) != prompt_digest()
    assert prompt_digest() == prompt_digest(dict(ROUTE_QUESTIONS))


def test_an_llm_pick_at_p_055_over_four_tools_falls_back() -> None:
    """The same raw probability that would clear 0.5 is about 0.40 once normalized, so the route takes its default."""
    reply = json.loads(model_reply(tool="research"))
    reply["tool"]["probabilities"] = {"search": 0.15, "extract": 0.15, "research": 0.55, "browse": 0.15}
    module, _ = fake_openai(json.dumps(reply))
    route = execution.classify(router(LLMDecider(client=module.OpenAI())), "who won")
    assert route.labels["tool"].confidence == pytest.approx(0.4)
    assert route.method == "search" and ROUTE_FALLBACK_LOW_CONFIDENCE in route.fallback


def test_llm_probabilities_are_normalized_before_the_sum_check() -> None:
    """A confident answer whose probabilities drift from 1 is scaled, not rejected as a decider error."""
    reply = json.loads(model_reply())
    reply["tool"]["probabilities"] = {"search": 0.9, "extract": 0.2, "research": 0.1, "browse": 0.0}
    module, _ = fake_openai(json.dumps(reply))
    route = execution.classify(router(LLMDecider(client=module.OpenAI())), "who won")
    assert route.fallback == ()
    assert sum(route.labels["tool"].probabilities.values()) == pytest.approx(1.0)
    assert route.labels["tool"].probabilities["search"] == pytest.approx(0.75)


@pytest.mark.parametrize(
    "probabilities",
    [
        {"search": 0.0, "extract": 0.0},
        {"search": 1.2, "extract": -0.2},
        {"search": float("nan")},
    ],
)
def test_an_llm_distribution_that_cannot_be_scaled_is_a_decider_error(probabilities: dict[str, float]) -> None:
    reply = json.loads(model_reply())
    reply["tool"]["probabilities"] = {**dict.fromkeys(ROUTE_QUESTIONS["tool"]["criteria"], 0.0), **probabilities}
    module, _ = fake_openai(json.dumps(reply))
    decider = LLMDecider(client=module.OpenAI())
    with pytest.raises(ProviderError):
        decider.decide({"query": "q"}, ROUTE_QUESTIONS)
    assert execution.classify(router(decider), "who won").fallback == (ROUTE_FALLBACK_DECIDER_ERROR,)

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
    ROUTE_QUESTIONS,
)
from web_router.decisions import LLMDecider
from web_router.decisions.llm_prompt import prompt_digest
from web_router.exceptions import ProviderError, ProviderNotInstalledError


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


def test_the_llm_decider_sends_no_temperature_to_a_fixed_temperature_model() -> None:
    module, seen = fake_openai(model_reply())
    model = f"{EVALS_FIXED_TEMPERATURE_MODELS[0]}-mini"
    LLMDecider(model, client=module.OpenAI()).decide({"query": "q"}, ROUTE_QUESTIONS)
    assert "temperature" not in seen["requests"][0]


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

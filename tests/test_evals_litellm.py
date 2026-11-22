"""LiteLLMJudge: any model LiteLLM reaches, with LiteLLM faked. No network."""

from __future__ import annotations

import os
import sys
import types
from typing import Any

import pytest

from web_router.constants_evals import EVALS_LITELLM_IMPORT_ENV
from web_router.evals import LiteLLMJudge
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError


class FakeLiteLLM(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("litellm")
        self.requests: list[dict[str, Any]] = []
        self.missing: list[str] = []
        self.finish = "stop"

    def validate_environment(self, model: str, api_base: str | None = None) -> dict[str, Any]:
        return {"keys_in_environment": not self.missing, "missing_keys": self.missing}

    def completion(self, **request: Any) -> Any:
        self.requests.append(request)
        message = types.SimpleNamespace(content="A")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message, finish_reason=self.finish)])

    async def acompletion(self, **request: Any) -> Any:
        return self.completion(**request)


@pytest.fixture
def litellm(monkeypatch: pytest.MonkeyPatch) -> FakeLiteLLM:
    fake = FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    for name in EVALS_LITELLM_IMPORT_ENV:
        monkeypatch.delenv(name, raising=False)
    return fake


def test_the_litellm_judge_grades_through_litellm(litellm: FakeLiteLLM) -> None:
    judge = LiteLLMJudge("openrouter/z-ai/glm-5")
    assert judge.grade("Who?", "Alice", "Alice") == "correct"
    (request,) = litellm.requests
    assert request["model"] == "openrouter/z-ai/glm-5"
    assert request["temperature"] == 0.0 and request["drop_params"] is True
    assert [m["role"] for m in request["messages"]] == ["user"]
    assert "api_base" not in request


async def test_the_litellm_judge_synthesizes_async_with_the_system_prompt(litellm: FakeLiteLLM) -> None:
    judge = LiteLLMJudge("gemini/gemini-3-pro", base_url="https://models.example.com", temperature=None)
    assert await judge.asynthesize("Who?", []) == "A"
    (request,) = litellm.requests
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    assert request["api_base"] == "https://models.example.com"
    assert "temperature" not in request


def test_importing_litellm_reads_no_dotfile_and_fetches_nothing(litellm: FakeLiteLLM) -> None:
    LiteLLMJudge("openai/gpt-6-luna").check()
    assert {name: os.environ[name] for name in EVALS_LITELLM_IMPORT_ENV} == EVALS_LITELLM_IMPORT_ENV


def test_a_callers_own_litellm_settings_are_kept(litellm: FakeLiteLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_MODE", "DEV")
    LiteLLMJudge("openai/gpt-6-luna").check()
    assert os.environ["LITELLM_MODE"] == "DEV"


def test_the_litellm_judge_names_the_missing_key(litellm: FakeLiteLLM) -> None:
    litellm.missing = ["OPENROUTER_API_KEY"]
    with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
        LiteLLMJudge("openrouter/z-ai/glm-5").check()


def test_an_ollama_endpoint_is_not_a_missing_key(litellm: FakeLiteLLM) -> None:
    litellm.missing = ["OLLAMA_API_BASE"]
    LiteLLMJudge("ollama/llama3").check()


def test_the_litellm_judge_without_the_sdk_names_its_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "litellm", None)
    with pytest.raises(ProviderNotInstalledError, match="evals-litellm"):
        LiteLLMJudge("openrouter/z-ai/glm-5").check()


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_a_reply_cut_short_is_an_error_not_a_grade(litellm: FakeLiteLLM, finish: str) -> None:
    litellm.finish = finish
    with pytest.raises(ProviderError, match=finish):
        LiteLLMJudge("openrouter/z-ai/glm-5").grade("Who?", "Alice", "Alice")

"""Choosing a judge by name, the LiteLLM judge, and the scorer interface. No network."""

from __future__ import annotations

import os
import sys
import types
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from web_router.constants_evals import EVALS_DEFAULT_JUDGE_SPEC, EVALS_LITELLM_IMPORT_ENV
from web_router.evals import (
    AnthropicJudge,
    Judge,
    LiteLLMJudge,
    OpenAIJudge,
    Scorecard,
    Scorer,
    judge_from,
)
from web_router.exceptions import ConfigurationError, ProviderNotInstalledError
from web_router.types_citation import Citation


class MyJudge(Judge):
    name = "mine"

    def _complete(self, system: str, user: str) -> str:
        return "A"

    async def _acomplete(self, system: str, user: str) -> str:
        return "A"


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch) -> str:
    """A module of the caller's own, holding a judge and something that is not one."""
    module = types.ModuleType("my_judges")
    module.MyJudge = MyJudge  # type: ignore[attr-defined]
    module.NotAJudge = dict  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "my_judges", module)
    return "my_judges"


def test_the_default_spec_is_the_default_judges_label() -> None:
    judge = judge_from(EVALS_DEFAULT_JUDGE_SPEC)
    assert isinstance(judge, OpenAIJudge)
    assert judge.label == EVALS_DEFAULT_JUDGE_SPEC == "openai:gpt-4o"


@pytest.mark.parametrize(
    ("spec", "kind", "label"),
    [
        ("openai", OpenAIJudge, "openai:gpt-4o"),
        ("openai:gpt-6-luna", OpenAIJudge, "openai:gpt-6-luna"),
        ("anthropic", AnthropicJudge, "anthropic:claude-sonnet-5-5"),
        (" anthropic:claude-opus-5-5 ", AnthropicJudge, "anthropic:claude-opus-5-5"),
        ("litellm:openrouter/z-ai/glm-5", LiteLLMJudge, "litellm:openrouter/z-ai/glm-5"),
    ],
)
def test_a_built_in_judge_is_named_by_kind_and_model(spec: str, kind: type[Judge], label: str) -> None:
    judge = judge_from(spec)
    assert type(judge) is kind
    assert judge.label == label


def test_a_judge_of_your_own_is_named_by_module_and_class(plugin: str) -> None:
    judge = judge_from(f"{plugin}:MyJudge")
    assert isinstance(judge, MyJudge)
    assert judge.label == "mine"


@pytest.mark.parametrize("spec", ["", "nonsense", ":MyJudge", "litellm", "litellm:"])
def test_a_spec_naming_no_judge_is_refused(spec: str) -> None:
    with pytest.raises(ConfigurationError, match="names no"):
        judge_from(spec)


def test_a_module_that_does_not_load_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="Could not load the judge"):
        judge_from("no_such_module_here:Judge")


def test_a_class_missing_from_the_module_is_refused(plugin: str) -> None:
    with pytest.raises(ConfigurationError, match="Could not load the judge"):
        judge_from(f"{plugin}:Missing")


def test_what_is_not_a_judge_is_refused(plugin: str) -> None:
    with pytest.raises(ConfigurationError, match="not a Judge subclass"):
        judge_from(f"{plugin}:NotAJudge")


@pytest.mark.parametrize(
    "judge",
    [
        OpenAIJudge("glm-5", base_url="https://models.example.com/v1"),
        AnthropicJudge("glm-5", base_url="https://models.example.com"),
        LiteLLMJudge("openai/glm-5", api_base="https://models.example.com/v1"),
    ],
)
def test_a_judge_on_another_endpoint_names_its_host(judge: Judge) -> None:
    """The same model name on another host may be another model, so its figures must not combine with the vendor's."""
    assert judge.label.endswith("@models.example.com")


class FakeLiteLLM(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("litellm")
        self.requests: list[dict[str, Any]] = []
        self.missing: list[str] = []

    def validate_environment(self, model: str, api_base: str | None = None) -> dict[str, Any]:
        return {"keys_in_environment": not self.missing, "missing_keys": self.missing}

    def completion(self, **request: Any) -> Any:
        self.requests.append(request)
        message = types.SimpleNamespace(content="A")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

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
    judge = LiteLLMJudge("gemini/gemini-3-pro", api_base="https://models.example.com", temperature=None)
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


def test_the_litellm_judge_without_the_sdk_names_its_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "litellm", None)
    with pytest.raises(ProviderNotInstalledError, match="evals-litellm"):
        LiteLLMJudge("openrouter/z-ai/glm-5").check()


class FixedScorer(Scorer):
    name = "fixed-v1"

    def score(self, question: str, answer: str, citations: Sequence[Citation] = ()) -> Scorecard:
        dimensions = {"factual_accuracy": 8.0, "coverage": 6.0, "sources_cited": None if not citations else 9.0}
        return Scorecard(scorer=self.label, scale=(1, 10), dimensions=dimensions, overall=7.0)


async def test_a_scorer_of_your_own_scores_sync_and_async() -> None:
    scorer = FixedScorer()
    card = scorer.score("Who?", "Alice", [Citation(url="https://example.com")])
    assert card.scorer == "fixed-v1" and card.dimensions["sources_cited"] == 9.0
    assert (await scorer.ascore("Who?", "Alice")).dimensions["sources_cited"] is None


@pytest.mark.parametrize(("dimension", "overall"), [(11.0, 5.0), (5.0, 0.0)])
def test_a_scorecard_refuses_a_score_off_its_scale(dimension: float, overall: float) -> None:
    with pytest.raises(ValidationError, match="outside the scorecard's scale"):
        Scorecard(scorer="s", scale=(1, 10), dimensions={"coverage": dimension}, overall=overall)

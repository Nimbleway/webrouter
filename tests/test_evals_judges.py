"""Choosing a judge by name and the LiteLLM judge. No network."""

from __future__ import annotations

import sys
import types

import pytest

from web_router.constants_evals import EVALS_DEFAULT_JUDGE_SPEC
from web_router.evals import (
    AnthropicJudge,
    Judge,
    LiteLLMJudge,
    OpenAIJudge,
    judge_from,
)
from web_router.exceptions import ConfigurationError


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
        LiteLLMJudge("openai/glm-5", base_url="https://models.example.com/v1"),
    ],
)
def test_a_judge_on_another_endpoint_names_its_host(judge: Judge) -> None:
    """The same model name on another host may be another model, so its figures must not combine with the vendor's."""
    assert judge.label.endswith("@models.example.com")


@pytest.mark.parametrize(
    ("base_url", "host"),
    [
        ("https://user:secret@models.example.com:8443/v1", "models.example.com:8443"),
    ],
)
def test_a_label_keeps_the_port_and_drops_credentials(base_url: str, host: str) -> None:
    assert OpenAIJudge("glm-5", base_url=base_url).label == f"openai:glm-5@{host}"


async def test_a_judge_of_your_own_closes_without_holding_anything() -> None:
    judge = MyJudge()
    judge.close()
    await judge.aclose()
    await LiteLLMJudge("openai/gpt-6-luna").aclose()


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6.1-sol", "gpt-5-mini"])
def test_a_model_that_refuses_a_fixed_temperature_gets_none(model: str) -> None:
    """gpt-6-luna answers temperature=0 with a 400 ("only the default (1) value is supported")."""
    assert OpenAIJudge(model, api_key="k").temperature is None
    assert OpenAIJudge("gpt-4o", api_key="k").temperature == 0.0


def test_a_subclass_with_no_name_of_its_own_is_labelled_by_its_class() -> None:
    """It may grade differently, so its figures must not combine with the built-in judge's."""
    changed = type("GradingFails", (OpenAIJudge,), {})
    same = type("Metered", (OpenAIJudge,), {"name": OpenAIJudge.name})
    assert changed("gpt-4o", api_key="k").label == "GradingFails(openai):gpt-4o"
    assert same("gpt-4o", api_key="k").label == "openai:gpt-4o"
    assert type("Mine", (LiteLLMJudge,), {})("openai/x").label == "Mine(litellm):openai/x"

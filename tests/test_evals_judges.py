"""Choosing a judge by name, the LiteLLM judge, and the scorer interface. No network."""

from __future__ import annotations

import math
import sys
import types
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from web_router.constants_evals import EVALS_DEFAULT_JUDGE_SPEC
from web_router.evals import (
    AnthropicJudge,
    Judge,
    LiteLLMJudge,
    OpenAIJudge,
    Scorecard,
    Scorer,
    judge_from,
)
from web_router.exceptions import ConfigurationError
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
        ("https://key:secret@models.example.com/v1", "models.example.com"),
    ],
)
def test_a_label_keeps_the_port_and_drops_credentials(base_url: str, host: str) -> None:
    assert OpenAIJudge("glm-5", base_url=base_url).label == f"openai:glm-5@{host}"


async def test_a_judge_of_your_own_closes_without_holding_anything() -> None:
    judge = MyJudge()
    judge.close()
    await judge.aclose()
    await LiteLLMJudge("openai/gpt-6-luna").aclose()


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


@pytest.mark.parametrize("scale", [(10, 1), (5, 5), (1, math.inf), (-math.inf, 10)])
def test_a_scorecard_refuses_a_scale_that_is_not_one(scale: tuple[float, float]) -> None:
    with pytest.raises(ValidationError, match="finite lowest and highest"):
        Scorecard(scorer="s", scale=scale, dimensions={}, overall=None)


@pytest.mark.parametrize(("dimension", "overall"), [(11.0, 5.0), (5.0, 0.0)])
def test_a_scorecard_refuses_a_score_off_its_scale(dimension: float, overall: float) -> None:
    with pytest.raises(ValidationError, match="outside the scorecard's scale"):
        Scorecard(scorer="s", scale=(1, 10), dimensions={"coverage": dimension}, overall=overall)


def test_a_scorecard_carries_reasons_and_the_scorers_own_details() -> None:
    card = Scorecard(
        scorer="s-v2",
        scale=(1, 10),
        dimensions={"factual_accuracy": 9.0, "coverage": None},
        overall=9.0,
        reasons={"factual_accuracy": "Every claim checked out.", "coverage": "Not applicable: one fact asked."},
        details={"usage": [{"role": "judge", "input_tokens": 900}], "comparable": True},
    )
    assert Scorecard.model_validate(card.model_dump(mode="json")) == card


def test_a_reason_for_a_dimension_not_scored_is_refused() -> None:
    with pytest.raises(ValidationError, match="reason is given for tone"):
        Scorecard(scorer="s", scale=(1, 10), dimensions={"coverage": 5.0}, overall=5.0, reasons={"tone": "warm"})


def test_reasons_and_details_default_to_empty() -> None:
    card = Scorecard(scorer="s", scale=(1, 10), dimensions={}, overall=None)
    assert card.reasons == {} and card.details == {}

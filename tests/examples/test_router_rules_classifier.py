"""The keyless example routers: examples/router_rules.py and examples/router_classifier.py, offline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.examples.helpers import CANDIDATES, EXTRACT, load_example, request
from web_router import RouteDecision, WebRouter

rules = load_example("router_rules")
classifier = load_example("router_classifier")


class TestRules:
    def test_a_url_extracts(self) -> None:
        assert rules.route(request("read this", urls=("https://example.com/a",))).tools == ["extract"]

    @pytest.mark.parametrize("prompt", ["Compare A and B", "write a report on X", "analyse Y", "analyze Z"])
    def test_a_comparison_or_report_researches_then_searches(self, prompt: str) -> None:
        assert rules.route(request(prompt)).tools == ["research", "search"]

    def test_anything_else_has_no_opinion(self) -> None:
        assert rules.route(request("python asyncio tutorial")) == RouteDecision(tools=None)

    def test_a_word_inside_another_is_not_a_rule(self) -> None:
        assert rules.route(request("reporters near me")).tools is None

    def test_plans_through_the_router_offline(self) -> None:
        with WebRouter(router=rules.route) as router:
            plan = router.plan("read https://example.com/a")
        assert plan.tool == "extract"
        assert plan.decision.router == "route"

    def test_runs_as_a_script(self, capsys: pytest.CaptureFixture[str]) -> None:
        rules.main()
        out = capsys.readouterr().out
        assert "-> extract" in out
        assert "-> search" in out


class _Model:
    """A fitted model's two attributes the router reads, with fixed probabilities."""

    classes_ = ("answer", "research", "search")

    def __init__(self, probabilities: tuple[float, ...]) -> None:
        self.probabilities = probabilities
        self.seen: list[list[str]] = []

    def predict_proba(self, prompts: list[str]) -> list[tuple[float, ...]]:
        self.seen.append(prompts)
        return [self.probabilities]


class _ExtractModel(_Model):
    classes_ = ("extract", "search")

    def __init__(self) -> None:
        super().__init__((0.9, 0.1))


class TestClassifier:
    def test_ranks_the_offered_tools_by_probability(self) -> None:
        model = _Model((0.2, 0.5, 0.3))
        decision = classifier.ClassifierRouter(model).route(request("analyze the market"))
        assert model.seen == [["analyze the market"]]
        assert decision.tools == ["research", "search", "answer"]
        assert decision.confidence == 0.5

    def test_leaves_out_a_tool_with_no_candidate(self) -> None:
        only_search = tuple(c for c in request().candidates if c.tool == "search")
        decision = classifier.ClassifierRouter(_Model((0.7, 0.2, 0.1))).route(request(candidates=only_search))
        assert decision.tools == ["search"]

    def test_ranks_extract_only_when_the_prompt_names_a_url(self) -> None:
        model = _ExtractModel()
        offered = (*CANDIDATES, EXTRACT)
        assert "extract" not in classifier.ClassifierRouter(model).route(request(candidates=offered)).tools
        with_url = request(candidates=offered, urls=("https://example.com/a",))
        assert classifier.ClassifierRouter(model).route(with_url).tools[0] == "extract"

    def test_no_offered_tool_is_no_opinion(self) -> None:
        router = classifier.ClassifierRouter(_Model((1.0, 0.0, 0.0)))
        assert router.route(request(candidates=())) == RouteDecision()

    @pytest.mark.parametrize("missing", ["joblib", "sklearn"])
    def test_without_scikit_learn_it_says_how_to_get_it(
        self, missing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        if missing == "sklearn":  # joblib alone installed, scikit-learn not
            monkeypatch.setitem(sys.modules, "joblib", object())
        monkeypatch.setitem(sys.modules, missing, None)  # importing it now raises ImportError
        classifier.main()
        assert "uv run --with scikit-learn" in capsys.readouterr().out


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> object:
    """The example's pipeline, trained, saved and loaded back with joblib; skips without scikit-learn."""
    joblib = pytest.importorskip("joblib")
    pytest.importorskip("sklearn")
    path: Path = tmp_path_factory.mktemp("model") / "router.joblib"
    classifier.train(path)
    return joblib.load(path)


class TestClassifierTrained:
    """The real pipeline, saved and loaded with joblib: skipped where scikit-learn is not installed."""

    def test_the_trained_model_routes_its_training_examples(self, model: object) -> None:
        router = classifier.ClassifierRouter(model)
        assert router.route(request("capital of australia")).tools[0] == "answer"
        assert router.route(request("analyze the market for home batteries in europe")).tools[0] == "research"

    def test_plans_through_the_router(self, model: object) -> None:
        with WebRouter(router=classifier.ClassifierRouter(model)) as router:
            plan = router.plan("get the text of https://example.com/page")
        assert plan.tool == "extract"

    def test_runs_as_a_script(self, model: object, capsys: pytest.CaptureFixture[str]) -> None:
        classifier.main()
        assert "-> extract" in capsys.readouterr().out

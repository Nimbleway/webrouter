"""route-eval on a mixed set through the merge rule: a decision picks one method, its backends and its effort."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import path_row, write_frozen_set, write_mixed_set
from web_router import RouteDecision


@pytest.fixture(scope="module")
def route_eval_merge(route_eval: ModuleType) -> ModuleType:
    """The merge walk, as route-eval imported it."""
    return sys.modules["route_eval_merge"]


def test_a_decision_on_a_mixed_set_picks_one_tool_and_its_effort(
    route_eval: ModuleType, route_eval_merge: ModuleType, tmp_path: Path
) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    request = route_eval_merge.request_for(questions[0])
    assert request.pinned_tool is None and request.urls == ("frozen:pages",), "the rows keep no URL: a placeholder"
    assert not route_eval_merge.request_for(questions[-1]).urls, "no pages, no URL"
    assert {"research:parallel", "search:nimble", "extract:nimble"} <= {c.id for c in request.candidates}
    high = {"research": {"effort": "high"}}
    decision = RouteDecision(tools=["research"], providers={"research": ["parallel"]}, params=high)
    static = route_eval.static_router(route_eval.MIXED)
    outcome = route_eval.replay(lambda _: decision, questions[-1], static)
    assert outcome.served_by == "research:parallel:high" and not outcome.fallback and not outcome.router_failed
    unset = route_eval.replay(lambda _: RouteDecision(tools=["research"]), questions[-1], static)
    assert unset.served_by is None and unset.unscorable == 2, "no effort: each default, medium, which has no row"
    raised = route_eval.replay(lambda _: 1 / 0, questions[-1], static)
    assert raised.router_failed and raised.served_by == "search+extract:perplexity:top3", "the static path order"


def test_a_ranking_on_a_mixed_set_is_followed_by_the_static_path_order(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    static = route_eval.static_router(route_eval.MIXED)
    outcome = route_eval.replay(lambda _: ["research:perplexity:low"], questions[0], static)
    assert outcome.served_by == "search+extract:perplexity:top3" and outcome.fallback and not outcome.router_failed


@pytest.mark.parametrize("failure", [None, "router_error"])
def test_an_empty_or_failed_decision_on_a_mixed_set_stays_on_one_method(
    route_eval: ModuleType, route_eval_merge: ModuleType, tmp_path: Path, failure: str | None
) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    q = questions[-1]
    static = route_eval.static_router(route_eval.MIXED)
    walked, failed = route_eval_merge.walk(RouteDecision(), q, static, failure)
    assert walked == [p for p in static(q) if p.startswith("search+extract:")], "search's paths only, as production"
    assert failed is (failure is not None)


def test_a_research_backend_with_no_row_at_the_effort_is_counted_unscorable(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    decision = RouteDecision(
        tools=["research"], providers={"research": ["perplexity", "parallel"]}, params={"research": {"effort": "high"}}
    )
    outcome = route_eval.replay(lambda _: decision, questions[-1], route_eval.static_router(route_eval.MIXED))
    assert outcome.served_by == "research:parallel:high", "perplexity has no high row; the walk goes on"
    assert outcome.unscorable == 1 and route_eval.score([outcome])["unscorable"] == 1
    assert outcome.fallback, "the unscorable first choice keeps its place: parallel served second"
    assert outcome.cost_usd == pytest.approx(0.1), "the unscorable miss costs nothing"


def test_a_research_backend_with_no_effort_set_runs_at_its_default(route_eval: ModuleType, tmp_path: Path) -> None:
    rows = [
        path_row("q", "search+extract:nimble:top3", "incorrect"),
        path_row("q", "research:parallel:low", "incorrect"),
        path_row("q", "research:parallel:medium", "correct"),
    ]
    write_frozen_set(tmp_path, "mixed-default", rows)
    _, (q,) = route_eval.load_set("mixed-default", tmp_path)
    decision = RouteDecision(tools=["research"], providers={"research": ["parallel"]})
    outcome = route_eval.replay(lambda _: decision, q, route_eval.static_router(route_eval.MIXED))
    assert outcome.served_by == "research:parallel:medium" and outcome.correct, "parallel's default is medium"


def _default_mode_set(folder: Path) -> None:
    """One question; parallel's research has a default-mode row (right), a low one and a medium one (wrong)."""
    rows = [
        path_row("q", "search+extract:nimble:top3", "incorrect"),
        path_row("q", "research:parallel", "correct", cost=0.1),
        path_row("q", "research:parallel:low", "incorrect"),
        path_row("q", "research:parallel:medium", "incorrect"),
    ]
    write_frozen_set(folder, "mixed-default-row", rows)


@pytest.mark.parametrize(
    ("effort", "served"),
    [(None, "research:parallel"), ("medium", "research:parallel:medium"), ("low", "research:parallel:low")],
)
def test_no_effort_runs_the_default_mode_row_before_the_priced_default(
    route_eval: ModuleType, tmp_path: Path, effort: str | None, served: str
) -> None:
    _default_mode_set(tmp_path)
    _, (q,) = route_eval.load_set("mixed-default-row", tmp_path)
    params = None if effort is None else {"research": {"effort": effort}}
    decision = RouteDecision(tools=["research"], providers={"research": ["parallel"]}, params=params)
    outcome = route_eval.replay(lambda _: decision, q, route_eval.static_router(route_eval.MIXED))
    assert outcome.served_by == served and outcome.unscorable == 0


@pytest.mark.parametrize(("effort", "served"), [(None, "parallel"), ("medium", "parallel"), ("high", None)])
def test_a_one_method_research_set_matches_the_effort_too(
    route_eval: ModuleType, tmp_path: Path, effort: str | None, served: str | None
) -> None:
    rows = [path_row("q", "research:parallel", "correct", cost=0.1), path_row("q", "research:nimble", "incorrect")]
    write_frozen_set(tmp_path, "research-only", rows)
    _, (q,) = route_eval.load_set("research-only", tmp_path)
    params = None if effort is None else {"research": {"effort": effort}}
    decision = RouteDecision(providers={"research": ["parallel"]}, params=params)
    outcome = route_eval.replay(lambda _: decision, q, route_eval.static_router("research"))
    if served is None:
        assert outcome.unscorable == 2 and outcome.served_by is None, "a default-mode row is no high run, on either"
    else:
        assert outcome.served_by == served and outcome.unscorable == 0, "the default-mode row is medium's call"

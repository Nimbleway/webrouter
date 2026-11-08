"""route-eval on a mixed set: paths as candidates, search then read as the static order, and research by effort."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import path_row, route_row, write_frozen_set, write_mixed_set


def test_a_mixed_set_keys_each_row_by_its_path(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    method, questions = route_eval.load_set("mixed-tiny", tmp_path)

    assert method == route_eval.MIXED
    first, last = questions[0], questions[-1]
    assert {"research:parallel:low", "research:parallel:high", "answer:perplexity"} <= set(first.rows)
    assert "research:perplexity:low" in first.rows, "a failed row stays a candidate, as on any set"
    assert "extract:nimble" in first.rows and "extract:nimble" not in last.rows, "extract only where pages were given"


def test_an_extract_path_is_ranked_on_the_questions_where_it_is_a_candidate(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    write_mixed_set(tmp_path)
    summary = route_eval.evaluate_set("mixed-tiny", [], floor=0.05, folder=tmp_path)

    extract = summary["backends"]["extract:nimble"]
    assert (extract["correct"], extract["questions"]) == (4, 4), "a question given no pages is not a miss for extract"
    assert "extract:nimble" not in summary["recommended_order"], "4 counted questions are too few to rank"
    assert summary["too_few_questions"] == ["extract:nimble", "research:perplexity:low"]


def test_a_set_mixing_methods_that_are_not_paths_is_refused(route_eval: ModuleType, tmp_path: Path) -> None:
    write_frozen_set(tmp_path, "odd", [route_row("brave", "correct"), route_row("nimble", "correct", method="crawl")])
    with pytest.raises(SystemExit, match="odd mixes methods \\(crawl, search\\)"):
        route_eval.load_set("odd", tmp_path)


def test_a_mixed_sets_static_order_searches_then_reads_first(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    order = route_eval.static_router(route_eval.MIXED)(questions[0])

    assert order[:2] == ["search+extract:perplexity:top3", "search+extract:nimble:top3"], "auto's search order"
    assert order.index("extract:nimble") < order.index("answer:perplexity") < order.index("research:parallel:low")
    assert order.index("research:parallel:low") < order.index("research:parallel:high"), "lowest effort first"
    assert order.index("research:parallel:high") < order.index("research:perplexity:low"), "auto's research order"


def test_extract_paths_follow_autos_extract_order(route_eval: ModuleType) -> None:
    paths = sys.modules["route_eval_paths"]
    order = route_eval.STATIC_ORDER["extract"]
    pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1 :] if a > b]
    assert pairs, "auto's extract chain, read from the library, has a pair out of alphabetical order"
    first, second = pairs[0]
    ranked = sorted([f"extract:{first}", f"extract:{second}"], key=lambda p: paths.path_key(p, route_eval.STATIC_ORDER))
    assert ranked == [f"extract:{first}", f"extract:{second}"], "auto's order, not the name"


def test_the_oracle_takes_the_cheapest_right_path(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    assert route_eval.oracle_router(route_eval.MIXED)(questions[0])[0] == "extract:nimble"
    assert route_eval.oracle_router(route_eval.MIXED)(questions[16])[0] == "research:parallel:high"
    assert route_eval.oracle_router(route_eval.MIXED)(questions[-1])[0] == "research:perplexity:low", "cheapest"


def test_a_path_failed_on_a_rate_limit_is_passed_over_in_the_replay_as_any_failed_row(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    static = route_eval.static_router(route_eval.MIXED)

    outcome = route_eval.replay(lambda _: ["research:perplexity:low", "research:parallel:high"], questions[0], static)
    assert outcome.served_by == "research:parallel:high" and outcome.fallback and not outcome.router_failed
    assert outcome.latency_s is None, "the failed row's latency was not recorded, so the total is unknown"


def test_a_path_the_question_has_no_row_for_is_a_router_failure(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    outcome = route_eval.replay(lambda _: ["extract:nimble"], questions[-1], route_eval.static_router("mixed"))
    assert outcome.router_failed and outcome.served_by == "search+extract:perplexity:top3"


def test_an_unstated_research_cost_is_priced_at_its_effort(
    route_eval_rank: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[Any, ...]] = []
    monkeypatch.setattr(route_eval_rank.prices, "estimate", lambda *args: asked.append(args) or 0.1)
    route_eval_rank.cost_of(path_row("q", "research:parallel:high", "correct"))
    route_eval_rank.cost_of(path_row("q", "answer:parallel", "correct"))
    assert asked == [("parallel", "research", "high"), ("parallel", "answer", None)]


def test_a_mixed_set_scores_its_example_routers_against_static_and_oracle(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    write_mixed_set(tmp_path)
    summary = route_eval.evaluate_set("mixed-tiny", None, floor=0.05, folder=tmp_path)

    routers = summary["routers"]
    assert list(routers) == ["static", "oracle", *route_eval.DEFAULT_PATH_ROUTERS]
    for part in ("in_sample", "held_out"):
        assert routers["oracle"][part]["accuracy"] == 1.0, "research:parallel:high is right on every question"
        assert routers["static"][part]["accuracy"] == 0.0, "perplexity's search+extract, first in auto's order"
        low = routers["always:research:parallel:low"][part]
        assert low["regret_static"] == pytest.approx(-low["accuracy"]) and low["mean_cost_usd"] == 0.005
    assert summary["static_order"][0] == "search+extract:perplexity:top3"


def test_a_one_method_set_has_no_effort_table(route_eval: ModuleType, tmp_path: Path) -> None:
    write_frozen_set(tmp_path, "tiny", [route_row("brave", "correct", qid=f"q{i}") for i in range(4)])
    summary = route_eval.evaluate_set("tiny", [], floor=0.05, folder=tmp_path)
    assert summary["efforts"] == [] and "Research by effort" not in route_eval.markdown([summary], 0.05, {})


def test_an_always_router_on_a_mixed_set_must_name_a_path_in_it(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    with pytest.raises(SystemExit, match="research:nimble:low has no rows in mixed-tiny"):
        route_eval.evaluate_set("mixed-tiny", ["always:research:nimble:low"], floor=0.05, folder=tmp_path)


def test_a_backend_router_is_skipped_on_a_mixed_default_set_and_refused_on_a_named_one(
    route_eval: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_mixed_set(tmp_path)
    summary = route_eval.evaluate_set("mixed-tiny", ["always:nimble"], floor=0.05, folder=tmp_path, skip_absent=True)
    assert summary["skipped"] == ["always:nimble"] and "always:nimble" not in summary["routers"]
    with pytest.raises(SystemExit, match="nimble has no rows in mixed-tiny"):
        route_eval.evaluate_set("mixed-tiny", ["always:nimble"], floor=0.05, folder=tmp_path)

    load_set = route_eval.load_set
    monkeypatch.setattr(route_eval, "load_set", lambda name, _, joined: load_set(name, tmp_path, joined))
    monkeypatch.setattr(route_eval, "DEFAULT_SETS", ("mixed-tiny",))
    with pytest.raises(SystemExit, match="a candidate in none of the sets: mixed-tiny"):
        route_eval.main(["--router", "always:nimble"])

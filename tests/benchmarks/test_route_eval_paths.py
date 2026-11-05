"""route-eval on a mixed set: paths as candidates, search then read as the static order, and research by effort."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import route_row, write_frozen_set


def _path_row(qid: str, path: str, grade: str | None, **extra: Any) -> dict[str, Any]:
    method, provider, *mode = path.split(":")
    return route_row(provider, grade, qid=qid, method=method, mode=mode[0] if mode else "default", **extra)


def _perplexity_low(qid: str, i: int) -> dict[str, Any]:
    """Rate-limited on the first 15 questions, timed out on two, right on the last three."""
    if i < 15:
        return _path_row(qid, "research:perplexity:low", None, latency=None, error="rate_limit")
    if i < 17:
        return _path_row(qid, "research:perplexity:low", None, latency=None, error="timeout")
    return _path_row(qid, "research:perplexity:low", "correct", cost=0.002)


def _mixed_set(folder: Path) -> None:
    """20 questions; ``research:parallel:high`` right on all, ``extract:nimble`` asked on the first four only."""
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows += [
            _path_row(qid, "search+extract:nimble:top3", "correct" if i < 8 else "incorrect", cost=0.008),
            _path_row(qid, "search+extract:perplexity:top3", "incorrect", cost=0.007),
            _path_row(qid, "answer:perplexity", "correct" if i < 12 else "incorrect", cost=0.002),
            _path_row(qid, "research:parallel:low", "correct" if i < 16 else "incorrect", cost=0.005),
            _path_row(qid, "research:parallel:high", "correct", cost=0.1, latency=30.0),
            _perplexity_low(qid, i),
        ]
        if i < 4:
            rows.append(_path_row(qid, "extract:nimble", "correct", cost=0.001))
    write_frozen_set(folder, "mixed-tiny", rows)


def test_a_mixed_set_keys_each_row_by_its_path(route_eval: ModuleType, tmp_path: Path) -> None:
    _mixed_set(tmp_path)
    method, questions = route_eval.load_set("mixed-tiny", tmp_path)

    assert method == route_eval.MIXED
    first, last = questions[0], questions[-1]
    assert {"research:parallel:low", "research:parallel:high", "answer:perplexity"} <= set(first.rows)
    assert "research:perplexity:low" in first.rows, "a failed row stays a candidate, as on any set"
    assert "extract:nimble" in first.rows and "extract:nimble" not in last.rows, "extract only where pages were given"


def test_a_set_mixing_methods_that_are_not_paths_is_refused(route_eval: ModuleType, tmp_path: Path) -> None:
    write_frozen_set(tmp_path, "odd", [route_row("brave", "correct"), route_row("nimble", "correct", method="crawl")])
    with pytest.raises(SystemExit, match="odd mixes methods \\(crawl, search\\)"):
        route_eval.load_set("odd", tmp_path)


def test_a_mixed_sets_static_order_searches_then_reads_first(route_eval: ModuleType, tmp_path: Path) -> None:
    _mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    order = route_eval.static_router(route_eval.MIXED)(questions[0])

    assert order[:2] == ["search+extract:perplexity:top3", "search+extract:nimble:top3"], "auto's search order"
    assert order.index("extract:nimble") < order.index("answer:perplexity") < order.index("research:parallel:low")
    assert order.index("research:parallel:low") < order.index("research:parallel:high"), "lowest effort first"
    assert order.index("research:parallel:high") < order.index("research:perplexity:low"), "auto's research order"


def test_extract_paths_follow_autos_extract_order(route_eval: ModuleType) -> None:
    paths = sys.modules["route_eval_paths"]
    order = route_eval.STATIC_ORDER["extract"]
    assert len(order) > 1, "auto's extract chain, read from the library"
    first, second = order[0], order[1]
    ranked = sorted([f"extract:{second}", f"extract:{first}"], key=lambda p: paths.path_key(p, route_eval.STATIC_ORDER))
    assert ranked == [f"extract:{first}", f"extract:{second}"]


def test_the_oracle_takes_the_cheapest_right_path(route_eval: ModuleType, tmp_path: Path) -> None:
    _mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    assert route_eval.oracle_router(route_eval.MIXED)(questions[0])[0] == "extract:nimble"
    assert route_eval.oracle_router(route_eval.MIXED)(questions[16])[0] == "research:parallel:high"
    assert route_eval.oracle_router(route_eval.MIXED)(questions[-1])[0] == "research:perplexity:low", "cheapest"


def test_a_path_failed_on_the_account_is_passed_over_in_the_replay_as_any_failed_row(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    _mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    static = route_eval.static_router(route_eval.MIXED)

    outcome = route_eval.replay(lambda _: ["research:perplexity:low", "research:parallel:high"], questions[0], static)
    assert outcome.served_by == "research:parallel:high" and outcome.fallback and not outcome.router_failed
    assert outcome.latency_s is None, "the failed row's latency was not recorded, so the total is unknown"


def test_a_path_the_question_has_no_row_for_is_a_router_failure(route_eval: ModuleType, tmp_path: Path) -> None:
    _mixed_set(tmp_path)
    _, questions = route_eval.load_set("mixed-tiny", tmp_path)
    outcome = route_eval.replay(lambda _: ["extract:nimble"], questions[-1], route_eval.static_router("mixed"))
    assert outcome.router_failed and outcome.served_by == "search+extract:perplexity:top3"


def test_an_unstated_research_cost_is_priced_at_its_effort(
    route_eval_rank: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[Any, ...]] = []
    monkeypatch.setattr(route_eval_rank.prices, "estimate", lambda *args: asked.append(args) or 0.1)
    route_eval_rank.cost_of(_path_row("q", "research:parallel:high", "correct"))
    route_eval_rank.cost_of(_path_row("q", "answer:parallel", "correct"))
    assert asked == [("parallel", "research", "high"), ("parallel", "answer", None)]


def test_a_mixed_set_scores_its_example_routers_against_static_and_oracle(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    _mixed_set(tmp_path)
    summary = route_eval.evaluate_set("mixed-tiny", None, floor=0.05, folder=tmp_path)

    routers = summary["routers"]
    assert list(routers) == ["static", "oracle", *route_eval.DEFAULT_PATH_ROUTERS]
    for part in ("in_sample", "held_out"):
        assert routers["oracle"][part]["accuracy"] == 1.0, "research:parallel:high is right on every question"
        assert routers["static"][part]["accuracy"] == 0.0, "perplexity's search+extract, first in auto's order"
        low = routers["always:research:parallel:low"][part]
        assert low["regret_static"] == pytest.approx(-low["accuracy"]) and low["mean_cost_usd"] == 0.005
    assert summary["static_order"][0] == "search+extract:perplexity:top3"


def test_the_report_ends_with_research_by_effort(route_eval: ModuleType, tmp_path: Path) -> None:
    _mixed_set(tmp_path)
    summary = route_eval.evaluate_set("mixed-tiny", [], floor=0.05, folder=tmp_path)

    efforts = [
        (e["backend"], e["effort"], e["correct"], e["questions"], e["failed"], e["excluded"])
        for e in summary["efforts"]
    ]
    assert efforts == [
        ("parallel", "low", 16, 20, 0, 0),
        ("parallel", "high", 20, 20, 0, 0),
        ("perplexity", "low", 3, 5, 2, 15),
    ], "the rate-limited rows are left out of the count and shown apart; the timeouts are misses"
    report = route_eval.markdown([summary], 0.05, {})
    assert "| Order | Path |" in report and "measured on search rows" in report
    assert report.index("Recommended order") < report.index("Research by effort")
    assert "| `parallel` | low | 80% (16/20) | $0.0050 | 1.0s | 0 | 0 |" in report
    assert "| `perplexity` | low | 60% (3/5) | $0.0020 | 1.0s | 2 | 15 |" in report


def test_a_one_method_set_has_no_effort_table(route_eval: ModuleType, tmp_path: Path) -> None:
    write_frozen_set(tmp_path, "tiny", [route_row("brave", "correct", qid=f"q{i}") for i in range(4)])
    summary = route_eval.evaluate_set("tiny", [], floor=0.05, folder=tmp_path)
    assert summary["efforts"] == [] and "Research by effort" not in route_eval.markdown([summary], 0.05, {})


def test_an_always_router_on_a_mixed_set_must_name_a_path_in_it(route_eval: ModuleType, tmp_path: Path) -> None:
    _mixed_set(tmp_path)
    with pytest.raises(SystemExit, match="research:nimble:low has no rows in mixed-tiny"):
        route_eval.evaluate_set("mixed-tiny", ["always:research:nimble:low"], floor=0.05, folder=tmp_path)


def test_the_committed_tool_and_effort_set_replays_offline(route_eval: ModuleType) -> None:
    floor, _ = route_eval.noise_floor()
    summary = route_eval.evaluate_set("tools-effort-60", None, floor=floor)

    assert summary["method"] == "mixed" and summary["questions"] == 60
    assert summary["static_order"][0].startswith("search+extract:")
    assert {(e["backend"], e["effort"]) for e in summary["efforts"]} >= {("parallel", "max"), ("nimble", "low")}
    left_out = {p: b["excluded"] for p, b in summary["backends"].items() if b["excluded"]}
    assert left_out == {"answer:brave": 22, "research:perplexity:low": 56, "research:perplexity:medium": 41}
    oracle = summary["routers"]["oracle"]["held_out"]["accuracy"]
    assert all(r["held_out"]["accuracy"] <= oracle for r in summary["routers"].values())
    assert "tools-effort-60" in route_eval.DEFAULT_SETS

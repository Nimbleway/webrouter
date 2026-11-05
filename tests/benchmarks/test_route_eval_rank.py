"""route-eval's ranking: the rule, each backend's numbers over a set, and the noise floor -- all offline."""

from __future__ import annotations

from types import ModuleType

import pytest

from tests.benchmarks.conftest import route_question as _question
from tests.benchmarks.conftest import route_row as _row
from tests.benchmarks.conftest import route_stats as _stats
from web_router._chains import chains


def test_a_gap_wider_than_the_noise_floor_ranks_by_accuracy_whatever_the_cost(route_eval_rank: ModuleType) -> None:
    stats = {"cheap": _stats(0.80, 0.001), "dear": _stats(0.90, 0.050)}
    assert route_eval_rank.rank_backends(stats, noise_floor=0.02) == ["dear", "cheap"]


def test_within_the_noise_floor_known_cost_then_latency_then_name_decide(route_eval_rank: ModuleType) -> None:
    stats = {
        "best-dear": _stats(0.90, 0.010),
        "cheap": _stats(0.89, 0.005, latency=2.0),
        "cheap-fast": _stats(0.895, 0.005, latency=1.0),
        "b-twin": _stats(0.89, 0.005, latency=2.0),
    }
    assert route_eval_rank.rank_backends(stats, noise_floor=0.02) == ["cheap-fast", "b-twin", "cheap", "best-dear"]


def test_an_unknown_or_free_cost_never_moves_a_backend_up(route_eval_rank: ModuleType) -> None:
    stats = {"priced": _stats(0.88, 0.020), "unknown": _stats(0.90, None), "free": _stats(0.90, 0.0)}
    order = route_eval_rank.rank_backends(stats, noise_floor=0.03)
    assert order[0] == "priced", "inside the floor, a known price beats a missing or $0 one"
    assert set(order[1:]) == {"unknown", "free"}


def test_the_keyless_tier_stays_last_however_well_it_scores(route_eval_rank: ModuleType) -> None:
    stats = {"you-mcp": _stats(0.99, 0.0), "nimble": _stats(0.50, 0.005)}
    assert route_eval_rank.rank_backends(stats, noise_floor=0.01) == ["nimble", "you-mcp"]


def test_backend_stats_leave_an_unrecorded_latency_out_of_the_median(route_eval_rank: ModuleType) -> None:
    questions = [
        _question(route_eval_rank, _row("brave", "correct", latency=None, qid="q1")),
        _question(route_eval_rank, _row("brave", "correct", latency=2.0, qid="q2")),
    ]
    stats = route_eval_rank.backend_stats(questions)["brave"]
    assert stats["latency_p50"] == 2.0 and stats["correct"] == 2


def test_the_noise_floor_is_the_twin_runs_widest_gap(route_eval_rank: ModuleType) -> None:
    floor, pairs = route_eval_rank.noise_floor()
    assert pairs["webiq"] == (51 / 254, 54 / 254), "WebIQ's correct answers on SealQA hard, then on its rerun"
    assert floor == pytest.approx(3 / 254), "the widest gap, WebIQ's three questions"
    assert floor == pytest.approx(chains()["noise_floor"], abs=1e-6), "the floor chains.json was ranked with"


def test_the_best_are_every_backend_within_the_floor_of_the_most_accurate_wherever_cost_put_them(
    route_eval_rank: ModuleType,
) -> None:
    stats = {"accurate": _stats(0.90, 0.050), "cheap": _stats(0.89, 0.001), "far": _stats(0.70, 0.001)}
    ranked, best = route_eval_rank.ranked_with_best(stats, noise_floor=0.02)
    assert ranked == route_eval_rank.rank_backends(stats, noise_floor=0.02) == ["cheap", "accurate", "far"]
    assert best == {"cheap", "accurate"}
    assert route_eval_rank.ranked_with_best({}, noise_floor=0.02) == ([], frozenset())


@pytest.mark.parametrize("error", ["judge", "leaked"])
def test_backend_stats_count_an_ungraded_row_as_served_as_the_replay_does(
    route_eval_rank: ModuleType, error: str
) -> None:
    questions = [
        _question(route_eval_rank, _row("tavily", None, latency=3.0, error=error, qid="q1")),
        _question(route_eval_rank, _row("tavily", "correct", latency=1.0, qid="q2")),
        _question(route_eval_rank, _row("tavily", None, latency=9.0, error="timeout", qid="q3")),
    ]
    stats = route_eval_rank.backend_stats(questions)["tavily"]
    assert stats["served"] == 2 and stats["failed"] == 1, "the backend answered; only grading did not happen"
    assert stats["correct"] == 1 and stats["accuracy_all"] == pytest.approx(1 / 3)
    assert stats["latency_p50"] == 2.0 and stats["cost_usd"] == pytest.approx(0.008), "an answered call is billed"


def test_a_row_failed_on_a_rate_limit_or_quota_is_left_out_not_counted_a_miss(route_eval_rank: ModuleType) -> None:
    questions = [
        _question(route_eval_rank, _row("you-mcp", None, error="rate_limit", qid="q0")),
        _question(route_eval_rank, _row("you-mcp", None, error="quota", qid="q1")),
        _question(route_eval_rank, _row("you-mcp", "correct", qid="q2")),
        _question(route_eval_rank, _row("you-mcp", None, error="http_5xx", qid="q3")),
    ]
    stats = route_eval_rank.backend_stats(questions)["you-mcp"]
    assert stats["excluded"] == 2 and stats["questions"] == 2
    assert stats["accuracy_all"] == 0.5, "the 5xx is still a miss; the rate limit and the quota are not graded"

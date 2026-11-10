"""route-eval's router comparison: where two routers part ways, its McNemar p-value and Wilson intervals, by hand."""

from __future__ import annotations

from types import ModuleType

import pytest

from tests.benchmarks._route_eval_choices import ROUTER, _c


def test_two_routers_disagree_where_their_served_paths_differ(compare: ModuleType) -> None:
    other = [
        _c("q1", "answer", True, 0.005, 2.0),
        _c("q2", "search", True, 0.001, 5.0, path="search:y"),  # same tool, another backend
        _c("q3", "research", False, 1.0, None, path="research:x"),  # the same path
        _c("q4", "research", True, None, 60.0),
        _c("q5", "search", True, 0.001, 1.0),  # the first router was not scored on it
    ]
    c = compare.compare(ROUTER, other)

    assert c["questions"] == 4, "only the questions both were scored on"
    assert (c["disagree"], c["tool_disagree"]) == (3, 2)
    assert (c["only_first_right"], c["only_second_right"]) == (0, 1)
    assert c["mcnemar_p"] == 1.0, "one discordant question proves nothing"
    assert c["first"]["accuracy"] == 0.5 and c["second"]["accuracy"] == 0.75
    assert c["cost_unknown"] == 1, "q4's other cost is unknown: counted once, for the pair"
    assert c["first"]["mean_cost_usd"] == pytest.approx((0.5 + 0.002 + 1.0) / 3), "q4 left out of both means"
    assert c["second"]["mean_cost_usd"] == pytest.approx((0.005 + 0.001 + 1.0) / 3)
    patterns = {(p["first_tool"], p["second_tool"]): p for p in c["patterns"]}
    assert list(patterns) == [("search", "search"), ("search", "research"), ("research", "answer")]
    assert patterns[("research", "answer")]["extra_cost_usd"] == pytest.approx(0.495)
    assert patterns[("search", "research")]["cost_unknown"] == 1
    assert patterns[("search", "research")]["only_second_right"] == 1
    assert [d["question_id"] for d in c["disagreements"]] == ["q1", "q2", "q4"]


@pytest.mark.parametrize(
    ("only_first", "only_second", "p"),
    [(6, 2, 2 * (1 + 8 + 28) / 256), (0, 5, 2 / 32), (5, 0, 2 / 32), (3, 3, 1.0), (1, 9, 22 / 1024)],
)
def test_mcnemar_is_the_exact_two_sided_binomial_p(
    compare: ModuleType, only_first: int, only_second: int, p: float
) -> None:
    assert compare._mcnemar(only_first, only_second) == pytest.approx(p)


def test_a_small_p_value_never_prints_as_zero(matrix_report: ModuleType) -> None:
    assert matrix_report.p_value(2 * 37 / 256) == "0.29"
    assert matrix_report.p_value(2 / 2**20) == "1.9e-06"


def test_each_accuracy_carries_its_wilson_interval(compare: ModuleType, matrix_report: ModuleType) -> None:
    picked = [_c(f"q{i}", "search", i < 19, 0.001, 1.0) for i in range(30)]
    side = compare.compare(picked, picked)["first"]

    low, high = side["accuracy_ci95"]
    assert (low, high) == (pytest.approx(0.4551, abs=1e-4), pytest.approx(0.7813, abs=1e-4)), "19 of 30"
    assert matrix_report._accuracy(side) == "63% (46%-78%)"


def test_each_pair_is_compared_once_and_the_oracle_never(matrix: ModuleType, compare: ModuleType) -> None:
    routers = {name: {"choices": ROUTER} for name in ("static", "oracle", "mine", "rules")}
    pairs = [(c["router"], c["against"]) for c in compare.comparisons(routers, ["static", "rules"], "oracle")]

    assert pairs == [("mine", "static"), ("rules", "static"), ("mine", "rules")]
    assert set(matrix.matrices(routers, "oracle")) == {"static", "mine", "rules"}

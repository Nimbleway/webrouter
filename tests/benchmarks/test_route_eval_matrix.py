"""route-eval's cost-weighted tool confusion matrix and its router comparison: the maths by hand, then end to end."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import write_mixed_set


@pytest.fixture(scope="module")
def matrix(route_eval: ModuleType) -> ModuleType:
    """The matrix maths, as route-eval imported it."""
    return sys.modules["route_eval_matrix"]


@pytest.fixture(scope="module")
def matrix_report(route_eval: ModuleType) -> ModuleType:
    """The matrix's report, as route-eval imported it."""
    return sys.modules["route_eval_report_matrix"]


def _c(qid: str, tool: str, correct: bool, cost: float | None, latency: float | None, path: str = "") -> dict:
    """One held-out choice, as :func:`route_eval_matrix.choices` writes it."""
    return {
        "question_id": qid,
        "path": path or f"{tool}:x",
        "tool": tool,
        "correct": correct,
        "cost_usd": cost,
        "latency_s": latency,
    }


ORACLE = [
    _c("q1", "answer", True, 0.005, 2.0),
    _c("q2", "search", True, 0.001, 5.0),
    _c("q3", "search", False, 0.001, 5.0),  # no path right: the oracle walked the static order
    _c("q4", "research", True, 0.5, 60.0),
]
ROUTER = [
    _c("q1", "research", True, 0.5, 80.0),  # right, but a research bill where an answer did
    _c("q2", "search", True, 0.002, 4.0),  # the best tool, another backend
    _c("q3", "research", False, 1.0, None),  # nobody was right; its time unrecorded
    _c("q4", "search", False, 0.001, 3.0),  # cheap and wrong
]


# The matrix, checked by hand


def test_each_cell_counts_its_questions_and_the_extra_over_the_oracle(matrix: ModuleType) -> None:
    m = matrix.confusion(ROUTER, ORACLE)

    assert m["chosen_tools"] == ["search", "research"]
    assert m["best_tools"] == ["search", "answer", "research", "none right"], "tool order, then none right"
    research_for_answer = m["cells"]["research"]["answer"]
    assert research_for_answer["questions"] == 1 and research_for_answer["correct"] == 1
    assert research_for_answer["extra_cost_usd"] == pytest.approx(0.495), "$0.50 against $0.005"
    assert research_for_answer["extra_latency_s"] == pytest.approx(78.0)
    assert m["cells"]["search"]["search"]["extra_cost_usd"] == pytest.approx(0.001), "the diagonal has a cost too"
    cheap_and_wrong = m["cells"]["search"]["research"]
    assert cheap_and_wrong["extra_cost_usd"] == pytest.approx(-0.499), "signed: cheaper, and paid in accuracy"
    assert cheap_and_wrong["overspend_usd"] == 0.0
    nobody = m["cells"]["research"]["none right"]
    assert nobody["extra_cost_usd"] == pytest.approx(0.999)
    assert (nobody["latency_unknown"], nobody["extra_latency_s"]) == (1, 0.0), "unknown is counted, never summed"


def test_misroutes_are_the_off_diagonal_questions_some_path_got_right(matrix: ModuleType) -> None:
    m = matrix.confusion(ROUTER, ORACLE)

    misrouted = m["misrouted"]
    assert misrouted["questions"] == 2, "q1 and q4; q2 is the best tool, nobody got q3 right"
    assert misrouted["correct"] == 1
    assert misrouted["extra_cost_usd"] == pytest.approx(0.495 - 0.499)
    assert misrouted["overspend_usd"] == pytest.approx(0.495), "only the dearer choice"
    assert misrouted["extra_latency_s"] == pytest.approx(78.0 - 57.0)
    assert m["misroute_cost_per_question_usd"] == pytest.approx(-0.004 / 4), "over every question scored"
    assert m["misroute_latency_per_question_s"] == pytest.approx(21.0 / 4)
    assert m["all"]["questions"] == 4
    assert m["all"]["extra_cost_usd"] == pytest.approx(0.495 + 0.001 + 0.999 - 0.499)
    assert m["all"]["latency_unknown"] == 1


def test_an_unknown_cost_is_counted_unknown_not_zero(matrix: ModuleType) -> None:
    router = [_c("q1", "research", True, None, 80.0), *ROUTER[1:]]
    m = matrix.confusion(router, ORACLE)

    cell = m["cells"]["research"]["answer"]
    assert (cell["cost_unknown"], cell["extra_cost_usd"], cell["extra_latency_s"]) == (1, 0.0, 78.0)
    assert m["misrouted"]["extra_cost_usd"] == pytest.approx(-0.499)


def test_a_total_that_left_questions_out_says_how_many(matrix: ModuleType, matrix_report: ModuleType) -> None:
    router = [_c("q1", "research", True, None, None), *ROUTER[1:]]
    row = matrix_report.matrix_section({"confusion": {"r": matrix.confusion(router, ORACLE)}})[3]

    assert row.count("(1 unknown)") >= 4, row


def test_the_oracle_against_itself_misroutes_nothing_and_costs_nothing_extra(matrix: ModuleType) -> None:
    m = matrix.confusion(ORACLE, ORACLE)

    assert m["misrouted"]["questions"] == 0 and m["misroute_cost_per_question_usd"] == 0.0
    assert m["all"]["extra_cost_usd"] == 0.0 and m["all"]["extra_latency_s"] == 0.0
    assert matrix.confusion([], [])["misroute_cost_per_question_usd"] is None


def test_a_paths_tool_is_its_method_and_an_unserved_question_has_its_own_row(matrix: ModuleType) -> None:
    mixed = "mixed"
    assert matrix.tool_of("search+extract:nimble:top3", mixed) == "search", "search then read is a search"
    assert matrix.tool_of("research:parallel:low", mixed) == "research"
    assert matrix.tool_of("nimble", "search") == "search", "a one-method set's backend runs the set's method"
    assert matrix.tool_of(None, mixed) == matrix.UNSERVED
    assert matrix.ordered(["none right", "unserved", "zeta", "research", "search"]) == [
        "search",
        "research",
        "zeta",
        "unserved",
        "none right",
    ]


# The comparison, checked by hand


def test_two_routers_disagree_where_their_served_paths_differ(matrix: ModuleType) -> None:
    other = [
        _c("q1", "answer", True, 0.005, 2.0),
        _c("q2", "search", True, 0.001, 5.0, path="search:y"),  # same tool, another backend
        _c("q3", "research", False, 1.0, None, path="research:x"),  # the same path
        _c("q4", "research", True, None, 60.0),
        _c("q5", "search", True, 0.001, 1.0),  # the first router was not scored on it
    ]
    c = matrix.compare(ROUTER, other)

    assert c["questions"] == 4, "only the questions both were scored on"
    assert (c["disagree"], c["tool_disagree"]) == (3, 2)
    assert (c["only_first_right"], c["only_second_right"]) == (0, 1)
    assert c["first"]["accuracy"] == 0.5 and c["second"]["accuracy"] == 0.75
    assert c["first"]["mean_cost_usd"] == pytest.approx((0.5 + 0.002 + 1.0 + 0.001) / 4)
    assert c["second"]["cost_unknown"] == 1
    assert c["second"]["mean_cost_usd"] == pytest.approx((0.005 + 0.001 + 1.0) / 3)
    patterns = {(p["first_tool"], p["second_tool"]): p for p in c["patterns"]}
    assert list(patterns) == [("search", "search"), ("search", "research"), ("research", "answer")]
    assert patterns[("research", "answer")]["extra_cost_usd"] == pytest.approx(0.495)
    assert patterns[("search", "research")]["cost_unknown"] == 1
    assert patterns[("search", "research")]["only_second_right"] == 1
    assert [d["question_id"] for d in c["disagreements"]] == ["q1", "q2", "q4"]


def test_each_pair_is_compared_once_and_the_oracle_never(matrix: ModuleType) -> None:
    routers = {name: {"choices": ROUTER} for name in ("static", "oracle", "mine", "rules")}
    pairs = [(c["router"], c["against"]) for c in matrix.comparisons(routers, ["static", "rules"], "oracle")]

    assert pairs == [("mine", "static"), ("rules", "static"), ("mine", "rules")]
    assert set(matrix.matrices(routers, "oracle")) == {"static", "mine", "rules"}


# The report and the command line


def test_a_cell_reads_questions_right_extra_cost_and_time(matrix: ModuleType, matrix_report: ModuleType) -> None:
    m = matrix.confusion(ROUTER, ORACLE)
    assert matrix_report.cell_text(m["cells"]["research"]["answer"]) == "1 (1 right), +$0.4950, +78.0s"
    assert matrix_report.cell_text(m["cells"]["search"]["research"]) == "1 (0 right), -$0.4990, -57.0s"
    assert matrix_report.cell_text(m["cells"]["research"]["none right"]).endswith("(1 time unknown)")
    assert matrix_report.cell_text(None) == "-"


def test_a_mixed_set_reports_each_routers_matrix_and_where_it_parts_from_static(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    write_mixed_set(tmp_path)
    summary = route_eval.evaluate_set(
        "mixed-tiny",
        ["always:research:parallel:high"],
        floor=0.05,
        folder=tmp_path,
        against=["always:answer:perplexity"],
    )

    assert list(summary["routers"]) == [
        "static",
        "oracle",
        "always:research:parallel:high",
        "always:answer:perplexity",
    ], "an --against router is scored too"
    assert "oracle" not in summary["confusion"]
    high = summary["confusion"]["always:research:parallel:high"]
    assert high["chosen_tools"] == ["research"] and high["all"]["questions"] == summary["held_out"]
    assert high["all"]["overspend_usd"] > 0, "always the dearest path: it pays more than the oracle"
    pairs = [(c["router"], c["against"]) for c in summary["comparisons"]]
    assert pairs == [
        ("always:research:parallel:high", "static"),
        ("always:answer:perplexity", "static"),
        ("always:research:parallel:high", "always:answer:perplexity"),
    ]
    report = route_eval.markdown([summary], 0.05, {})
    assert "### Tool confusion, held out" in report and "### Where routers disagree, held out" in report
    assert "#### `always:research:parallel:high` against `static`" in report
    assert "| Ran \\ best |" in report


def test_main_compares_against_a_named_router_and_writes_the_matrix(
    route_eval: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_mixed_set(tmp_path / "frozen")
    load_set = route_eval.load_set
    monkeypatch.setattr(route_eval, "load_set", lambda name, _, joined: load_set(name, tmp_path / "frozen", joined))
    out = tmp_path / "numbers.json"
    route_eval.main(
        ["--set", "mixed-tiny", "--router", "static", "--against", "always:answer:perplexity", "--json", str(out)]
    )

    s = json.loads(out.read_text(encoding="utf-8"))["sets"][0]
    assert set(s["confusion"]) == {"static", "always:answer:perplexity"}
    assert [(c["router"], c["against"]) for c in s["comparisons"]] == [("always:answer:perplexity", "static")]
    choices: list[dict[str, Any]] = s["routers"]["static"]["choices"]
    assert len(choices) == s["held_out"] and {"question_id", "path", "tool"} <= set(choices[0])
    assert "Where routers disagree" in capsys.readouterr().out

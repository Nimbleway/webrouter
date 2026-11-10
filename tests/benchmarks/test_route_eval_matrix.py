"""route-eval's cost-weighted tool confusion matrix: the maths by hand, then the report end to end."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks._route_eval_choices import (  # noqa: F401 -- the *_fixture names are fixtures
    ORACLE,
    RIGHT,
    ROUTER,
    _c,
    matrix_fixture,
    matrix_report_fixture,
)
from tests.benchmarks.conftest import write_mixed_set

# The matrix, checked by hand


def test_each_cell_counts_its_questions_and_what_it_spent_beyond_the_oracle(matrix: ModuleType) -> None:
    m = matrix.confusion(ROUTER, ORACLE)

    assert m["chosen_tools"] == ["search", "research"]
    assert m["best_tools"] == ["search", "answer", "research", "none right"], "tool order, then none right"
    research_for_answer = m["cells"]["research"]["answer"]
    assert research_for_answer["questions"] == 1 and research_for_answer["correct"] == 1
    assert research_for_answer["overspend_usd"] == pytest.approx(0.495), "$0.50 against $0.005"
    assert research_for_answer["overtime_s"] == pytest.approx(78.0)
    assert m["cells"]["search"]["search"]["overspend_usd"] == pytest.approx(0.001), "the diagonal has a cost too"
    cheap_and_wrong = m["cells"]["search"]["research"]
    assert (cheap_and_wrong["overspend_usd"], cheap_and_wrong["overtime_s"]) == (0.0, 0.0), "cheaper adds nothing"
    assert cheap_and_wrong["extra_cost_usd"] == pytest.approx(-0.499), "the signed extra stays, for --json"
    nobody = m["cells"]["research"]["none right"]
    assert nobody["overspend_usd"] == pytest.approx(0.999)
    assert (nobody["latency_unknown"], nobody["overtime_s"]) == (1, 0.0), "unknown is counted, never summed"


def test_off_the_oracle_is_split_into_accuracy_lost_and_cost_regret(matrix: ModuleType) -> None:
    m = matrix.confusion(ROUTER, ORACLE)

    wrong, dear = m["wrong_tool"], m["not_cheapest"]
    assert (wrong["questions"], wrong["correct"]) == (1, 0), "q4: research was right, search was not"
    assert (dear["questions"], dear["correct"]) == (1, 1), "q1: right on research where answer was cheaper"
    assert dear["overspend_usd"] == pytest.approx(0.495), "charged against the cheapest right path"
    assert m["oracle_correct"] == 3 and m["accuracy_lost"] == pytest.approx(1 / 4)
    assert m["all"]["overspend_usd"] == pytest.approx(0.495 + 0.001 + 0.999), "positive extras only"
    assert m["all"]["extra_cost_usd"] == pytest.approx(0.495 + 0.001 + 0.999 - 0.499), "signed, for --json"
    assert m["overspend_per_question_usd"] == pytest.approx(1.495 / 4), "over every question scored"
    assert m["overtime_per_question_s"] == pytest.approx(78.0 / 4)
    assert m["all"]["latency_unknown"] == 1


def test_a_right_tool_is_on_target_even_when_it_is_not_the_cheapest_or_its_backend_was_wrong(
    matrix: ModuleType,
) -> None:
    router = [
        _c("q1", "research", False, 0.5, 80.0, right=RIGHT["q1"]),  # a right tool, wrong on this backend
        _c("q2", "search", False, 0.001, 5.0, right=RIGHT["q2"]),  # the best tool, wrong on another backend
        *ROUTER[2:],
    ]
    m = matrix.confusion(router, ORACLE)

    assert m["wrong_tool"]["questions"] == 1, "only q4; a right tool's wrong backend is outside the tool matrix"
    assert m["not_cheapest"]["questions"] == 0, "neither was right"
    assert m["accuracy_lost"] == pytest.approx(3 / 4), "but accuracy lost counts every cause"


def test_an_unknown_cost_is_counted_unknown_not_zero(matrix: ModuleType) -> None:
    router = [_c("q1", "research", True, None, 80.0, right=RIGHT["q1"]), *ROUTER[1:]]
    m = matrix.confusion(router, ORACLE)

    cell = m["cells"]["research"]["answer"]
    assert (cell["cost_unknown"], cell["overspend_usd"], cell["overtime_s"]) == (1, 0.0, 78.0)
    assert m["not_cheapest"]["cost_unknown"] == 1 and m["not_cheapest"]["overspend_usd"] == 0.0


def test_a_total_that_left_questions_out_says_how_many(matrix: ModuleType, matrix_report: ModuleType) -> None:
    router = [_c("q1", "research", True, None, None, right=RIGHT["q1"]), *ROUTER[1:]]
    row = matrix_report.matrix_section({"confusion": {"r": matrix.confusion(router, ORACLE)}})[3]

    assert row.count("(1 unknown)") == 3, row
    assert "| `r` | 4 | 1 | 1 | $0.0000 (1 unknown) | $1.0000 (1 unknown) |" in row, row
    assert "| 1 (25%) |" in row, "accuracy lost beside the overspend, its own column"


def test_the_oracle_against_itself_loses_nothing_and_overspends_nothing(matrix: ModuleType) -> None:
    m = matrix.confusion(ORACLE, ORACLE)

    assert m["wrong_tool"]["questions"] == 0 and m["not_cheapest"]["questions"] == 0
    assert m["accuracy_lost"] == 0.0 and m["overspend_per_question_usd"] == 0.0
    assert m["all"]["overspend_usd"] == 0.0 and m["all"]["overtime_s"] == 0.0
    assert matrix.confusion([], [])["overspend_per_question_usd"] is None


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


# The report and the command line


def test_a_cell_reads_questions_right_overspend_and_time_over(matrix: ModuleType, matrix_report: ModuleType) -> None:
    m = matrix.confusion(ROUTER, ORACLE)
    assert matrix_report.cell_text(m["cells"]["research"]["answer"]) == "1 (1 right), $0.4950, +78.0s"
    assert matrix_report.cell_text(m["cells"]["search"]["research"]) == "1 (0 right), $0.0000, +0.0s"
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
    every = [(c["router"], c["against"], c["questions"]) for c in summary["comparisons_every_question"]]
    assert every == [(r, a, 20) for r, a in pairs], "none is fit, so each pair is compared on every question too"
    report = route_eval.markdown([summary], 0.05, {})
    assert "### Tool confusion, held out" in report and "### Where routers disagree, held out" in report
    assert "### Where routers disagree, every question" in report
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
    assert len(choices) == s["held_out"] and {"question_id", "path", "tool", "right_tools"} <= set(choices[0])
    assert "Where routers disagree" in capsys.readouterr().out

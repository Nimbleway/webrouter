"""The answer scan's setup: the backends it asks, a search scan beside it, and answer scans merged."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import ANSWER_SETUP as SETUP
from tests.benchmarks.conftest import answer_scan_row as _row


def _write_search_scan(folder: Path, judge: str = "openai:gpt-4o") -> Path:
    folder.mkdir()
    (folder / "run.json").write_text(
        json.dumps({"limit": 12, "seed": 0, "max_results": 5, "judge": judge, "providers": ["tavily"]}),
        encoding="utf-8",
    )
    rows = [
        {"provider": "tavily", "question": f"q{i}", "target": "a", "latency": 0.5, "grade": "correct"} for i in range(6)
    ]
    rows += [{"provider": "tavily", "question": "elsewhere", "target": "a", "latency": 0.5, "grade": "incorrect"}]
    (folder / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return folder


def test_the_baseline_stands_beside_on_the_same_questions_only(answer_scan: ModuleType, tmp_path: Path) -> None:
    baseline = _write_search_scan(tmp_path / "search")
    rows = [_row("perplexity", f"q{i}", "correct" if i % 2 else "incorrect") for i in range(6)]
    summary = answer_scan.summarize(rows, {**SETUP, "providers": ["perplexity"], "baseline": str(baseline)})

    tavily = summary["baseline"]["overall"]["tavily"]
    assert (tavily["served"], tavily["correct"]) == (6, 6), "a question this scan did not ask is left out"
    report = answer_scan.markdown(summary)
    assert "## Against search, with the judge writing the answer" in report
    assert "| search + judge | `tavily` | 100% (6/6)" in report
    assert "| answer() | `perplexity` | 50% (3/6)" in report


def test_a_baseline_graded_by_another_judge_is_refused(answer_scan: ModuleType, tmp_path: Path) -> None:
    baseline = _write_search_scan(tmp_path / "search", judge="anthropic:claude")

    with pytest.raises(SystemExit, match="judge"):
        answer_scan.summarize(
            [_row("perplexity", "q0", "correct")], {**SETUP, "providers": ["perplexity"], "baseline": str(baseline)}
        )


def test_answer_scans_graded_by_different_judges_do_not_combine(answer_scan: ModuleType, tmp_path: Path) -> None:
    for name, judge in (("a", "openai:gpt-4o"), ("b", "anthropic:claude")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "run.json").write_text(json.dumps({**SETUP, "judge": judge, "providers": [name]}), encoding="utf-8")
        (folder / "rows.jsonl").write_text(json.dumps(_row(name, "q0", "correct")) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="judge"):
        answer_scan.combine([tmp_path / "a", tmp_path / "b"])


def test_a_mismatched_baseline_is_refused_before_anything_is_asked(answer_scan: ModuleType, tmp_path: Path) -> None:
    baseline = _write_search_scan(tmp_path / "search")

    with pytest.raises(SystemExit, match="seed"):
        answer_scan.setup_of(
            answer_scan.parse_args(
                ["--providers", "linkup", "--seed", "7", "--limit", "12", "--baseline", str(baseline)]
            )
        )


def test_with_no_answer_backend_configured_the_scan_says_how_to_add_one(
    answer_scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(answer_scan, "configured_answer_providers", list)
    args = answer_scan.parse_args([])

    with pytest.raises(SystemExit, match=r"\.env\.example"):
        answer_scan.setup_of(args)


def test_a_repeated_backend_is_asked_once(answer_scan: ModuleType) -> None:
    assert answer_scan.setup_of(answer_scan.parse_args(["--providers", "linkup,perplexity,linkup"]))["providers"] == [
        "linkup",
        "perplexity",
    ]


def test_a_baseline_with_other_csv_columns_is_refused(answer_scan: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "q.csv"
    path.write_text("problem,answer,question\nWho?,Alice,Where?\n", encoding="utf-8")
    csv = ["--providers", "linkup", "--questions", "csv", "--dataset", str(path)]
    ours = answer_scan.setup_of(answer_scan.parse_args(csv))
    baseline = tmp_path / "search"
    baseline.mkdir()
    (baseline / "run.json").write_text(json.dumps({**ours, "question_column": "question"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="question_column"):
        answer_scan.setup_of(answer_scan.parse_args([*csv, "--baseline", str(baseline)]))

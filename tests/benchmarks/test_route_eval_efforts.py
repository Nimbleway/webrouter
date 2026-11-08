"""route-eval on a set mixing methods: research by effort, and the committed tool and effort set."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from tests.benchmarks.conftest import write_mixed_set


def test_the_report_ends_with_research_by_effort(route_eval: ModuleType, tmp_path: Path) -> None:
    write_mixed_set(tmp_path)
    summary = route_eval.evaluate_set("mixed-tiny", [], folder=tmp_path)

    efforts = [
        (e["backend"], e["effort"], e["correct"], e["questions"], e["failed"], e["excluded"])
        for e in summary["efforts"]
    ]
    assert efforts == [
        ("parallel", "low", 16, 20, 0, 0),
        ("parallel", "high", 20, 20, 0, 0),
        ("perplexity", "low", 3, 5, 2, 15),
    ], "rows failed on a rate-limit error are left out of the count and shown apart; the timeouts are misses"
    report = route_eval.markdown([summary], 0.05, {})
    assert "| Order | Path |" in report and "search's (`sealqa-hard-254` vs" in report, "the search floor, named"
    assert "no rerun of mixed is frozen" in report
    assert "| too few questions | `research:perplexity:low` | 60% (3/5) |" in report, "listed after, unranked"
    assert report.index("Recommended order") < report.index("Research by effort")
    assert "| `parallel` | low | 80% (16/20) | $0.0050 | 1.0s | 0 | 0 |" in report
    assert "| `perplexity` | low | 60% (3/5) | $0.0020 | 1.0s | 2 | 15 |" in report


def test_the_committed_tool_and_effort_set_replays_offline(route_eval: ModuleType) -> None:
    summary = route_eval.evaluate_set("tools-effort-60", None)

    assert summary["method"] == "mixed" and summary["questions"] == 60
    assert summary["static_order"][0].startswith("search+extract:")
    assert {(e["backend"], e["effort"]) for e in summary["efforts"]} >= {("parallel", "max"), ("nimble", "low")}
    left_out = {p: b["excluded"] for p, b in summary["backends"].items() if b["excluded"]}
    assert left_out == {"answer:brave": 22, "research:perplexity:low": 56, "research:perplexity:medium": 41}
    few = ["extract:nimble", "research:perplexity:low", "research:perplexity:medium"]
    assert summary["too_few_questions"] == few, "10, 4 and 19 counted; answer:brave's 38 is enough"
    assert not set(few) & set(summary["recommended_order"])
    assert summary["noise_floor"]["measured_on"] == "search" and summary["noise_floor"]["fallback"]
    oracle = summary["routers"]["oracle"]["held_out"]["accuracy"]
    assert all(r["held_out"]["accuracy"] <= oracle for r in summary["routers"].values())
    assert "tools-effort-60" in route_eval.DEFAULT_SETS

"""The provider scan's report and its merging of scans -- the tables it builds, not the searches it runs."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "run.py"


@pytest.fixture(scope="module")
def scan() -> ModuleType:
    # Loaded under its own name: the agent benchmark's script is also called run.py.
    spec = importlib.util.spec_from_file_location("provider_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(provider: str, question: str, grade: str | None, topic: str = "Science", **extra: Any) -> dict[str, Any]:
    failed = grade is None
    return {
        "provider": provider,
        "question": question,
        "target": "a",
        "latency": 1.0,
        "answer": None if failed else "a",
        "grade": grade,
        "error": "HTTP 402" if failed else None,
        "failed_at": "search" if failed else None,
        "topic": topic,
        "answer_type": "Date",
        **extra,
    }


SETUP = {"limit": 12, "seed": 0, "max_results": 5, "judge": "openai:gpt-4o", "started": "2026-09-30T00:00:00+00:00"}


def test_a_failed_search_is_left_out_of_accuracy_and_the_best_backend_per_topic_is_named(scan: ModuleType) -> None:
    rows = [_row("nimble", f"q{i}", "correct") for i in range(12)]
    rows += [_row("brave", f"q{i}", "correct" if i < 6 else "incorrect") for i in range(11)]
    rows += [_row("brave", "q11", None)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble", "brave"]})

    assert summary["overall"]["brave"]["accuracy"] == 6 / 11 and summary["overall"]["brave"]["failed"] == 1
    assert summary["by_topic"]["Science"]["questions"] == 12
    assert summary["by_topic"]["Science"]["leaders"] == ["nimble"]
    assert "**100%**" in scan.markdown(summary)


def test_a_group_too_small_to_tell_backends_apart_names_no_best(scan: ModuleType) -> None:
    rows = [_row("nimble", "q0", "correct", topic="Music"), _row("brave", "q0", "incorrect", topic="Music")]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble", "brave"]})

    assert summary["by_topic"]["Music"]["leaders"] == []


def _scan(folder: Path, providers: list[str], **setup: Any) -> Path:
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps({**SETUP, "providers": providers, **setup}))
    (folder / "rows.jsonl").write_text("".join(json.dumps(_row(p, "q0", "correct")) + "\n" for p in providers))
    return folder


def test_scans_of_the_same_questions_and_judge_combine_and_others_are_refused(scan: ModuleType, tmp_path: Path) -> None:
    first = _scan(tmp_path / "a", ["nimble", "brave"])
    later = _scan(tmp_path / "b", ["exa"])
    rows, setup = scan.combine([first, later])

    assert setup["providers"] == ["nimble", "brave", "exa"] and len(rows) == 3

    other_judge = _scan(tmp_path / "c", ["tavily"], judge="anthropic:claude-sonnet-5-5")
    with pytest.raises(SystemExit, match="judge"):
        scan.combine([first, other_judge])


MODES = {"tavily": {"ultra-fast": {"provider_options": {"search_depth": "ultra-fast"}, "note": "latency tier"}}}


def test_a_setup_names_a_backend_a_normalized_depth_or_a_vendor_mode(scan: ModuleType) -> None:
    assert scan.parse_setup("tavily", MODES) == ("tavily", None, {})
    assert scan.parse_setup("tavily@deep", MODES) == ("tavily", "deep", {})
    assert scan.parse_setup("tavily:ultra-fast", MODES) == ("tavily", None, {"search_depth": "ultra-fast"})
    with pytest.raises(SystemExit, match="depth"):
        scan.parse_setup("tavily@huge", MODES)
    with pytest.raises(SystemExit, match="it has: ultra-fast"):
        scan.parse_setup("tavily:turbo", MODES)


async def test_a_vendor_mode_adds_its_options_to_every_search_and_answers_under_its_own_name(
    scan: ModuleType,
) -> None:
    from tests.conftest import FakeProvider
    from web_router.types import SearchRequest

    inner = FakeProvider()
    mode = type(
        "fake:turbo", (scan.VendorMode,), {"name": "fake:turbo", "supported_parameters": inner.supported_parameters}
    )(inner, {"turbo": True})

    await mode.asearch(SearchRequest(query="q", provider_options={"kept": 1}))

    assert mode.name == "fake:turbo"
    assert inner.requests[-1].provider_options == {"kept": 1, "turbo": True}


def test_a_backend_that_failed_most_of_a_topic_cannot_lead_it(scan: ModuleType) -> None:
    rows = [_row("flaky", "q0", "correct")] + [_row("flaky", f"q{i}", None) for i in range(1, 10)]
    rows += [_row("steady", f"q{i}", "correct") for i in range(9)] + [_row("steady", "q9", "incorrect")]
    summary = scan.summarize(rows, {**SETUP, "providers": ["flaky", "steady"]})

    assert summary["by_topic"]["Science"]["accuracy"]["flaky"] == 1.0
    assert summary["by_topic"]["Science"]["leaders"] == ["steady"]


def test_p95_is_the_nearest_rank_evaluate_uses(scan: ModuleType) -> None:
    assert scan._pct([float(i) for i in range(1, 13)], 0.95) == 12.0


def test_a_plain_string_metadata_cell_gives_no_tags_rather_than_an_error(scan: ModuleType) -> None:
    assert scan._tags({"metadata": "not a mapping"}) == {}
    assert scan._tags({"metadata": {"topic": "Art"}}) == {"topic": "Art"}
    assert scan._tags({"topic": "Art"}) == {"topic": "Art"}


def test_scans_that_repeat_a_backend_or_ask_other_questions_are_refused(scan: ModuleType, tmp_path: Path) -> None:
    first = _scan(tmp_path / "a", ["nimble"])
    again = _scan(tmp_path / "b", ["nimble"])
    with pytest.raises(SystemExit, match="nimble again"):
        scan.combine([first, again])

    other = tmp_path / "c"
    other.mkdir()
    (other / "run.json").write_text(json.dumps({**SETUP, "providers": ["exa"]}))
    (other / "rows.jsonl").write_text(json.dumps(_row("exa", "a different question", "correct")) + "\n")
    with pytest.raises(SystemExit, match="different questions"):
        scan.combine([first, other])


def test_the_recommended_auto_order_ranks_plain_backends_and_says_which_the_run_tells_apart(
    scan: ModuleType,
) -> None:
    """A suggestion, opt-in: plain backends by accuracy, a mode or depth left out, a paste-ready chain.

    `close` answers 38 of 40 against `best`'s 40, inside its interval; `far`
    answers 20, well outside it; `best@deep` and `best:turbo` are setups, not
    chain members, however well they score.
    """
    counts = {"best": 40, "close": 38, "far": 20, "best@deep": 40, "best:turbo": 40, "silent": 0}
    rows = []
    for provider, correct in counts.items():
        for i in range(40):
            grade = None if provider == "silent" else ("correct" if i < correct else "incorrect")
            rows.append(_row(provider, f"q{i}", grade))
    summary = scan.summarize(rows, {**SETUP, "providers": list(counts)})

    assert scan.recommended_order(summary) == [("best", None), ("close", False), ("far", True)]

    report = scan.markdown(summary)
    section = report.split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 1 | `best` |" in section and "| best |" in section
    assert "| 2 | `close` |" in section and "not shown apart" in section
    assert "| 3 | `far` |" in section and "behind" in section
    assert "best@deep" not in section and "best:turbo" not in section and "`silent`" not in section
    assert 'WebRouter(provider_config={"auto": {"chain": {"search": ["best", "close", "far"]}}})' in section
    assert "openai:gpt-4o" in section, "the suggestion names the one judge it rests on"
    assert "opt-in" in section and "pull request backed by a scan" in section
    assert report.index("## Overall") < report.index("## Recommended auto order") < report.index("## By topic")


def test_a_scan_where_nothing_was_served_suggests_no_order(scan: ModuleType) -> None:
    summary = scan.summarize([_row("nimble", "q0", None)], {**SETUP, "providers": ["nimble"]})

    assert scan.recommended_order(summary) == []
    assert "no order to suggest" in scan.markdown(summary)


def test_a_backend_that_fails_most_searches_ranks_by_what_it_got_right_not_by_the_few_it_served(
    scan: ModuleType,
) -> None:
    """The you-mcp case: 30 of 40 searches failed, and the 10 it served were all right -- 100% on those."""
    rows = [_row("flaky", f"q{i}", "correct" if i < 10 else None) for i in range(40)]
    rows += [_row("steady", f"q{i}", "correct" if i < 30 else "incorrect") for i in range(40)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["flaky", "steady"]})

    assert summary["overall"]["flaky"]["accuracy"] == 1.0
    assert [p for p, _ in scan.recommended_order(summary)] == ["steady", "flaky"]
    section = scan.markdown(summary).split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 2 | `flaky` | 25% (10/40) |" in section and "| 30 |" in section


def test_the_recommended_order_never_names_auto_itself(scan: ModuleType) -> None:
    """As a member, `auto` would be a second auto on the default order, reaching unranked backends first."""
    rows = [_row(p, f"q{i}", "correct") for p in ("auto", "nimble") for i in range(12)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["auto", "nimble"]})

    assert [p for p, _ in scan.recommended_order(summary)] == ["nimble"]


def test_a_backend_that_answered_every_question_wrong_is_ranked_last_not_dropped(scan: ModuleType) -> None:
    rows = [_row("right", f"q{i}", "correct") for i in range(12)] + [
        _row("wrong", f"q{i}", "incorrect") for i in range(12)
    ]
    summary = scan.summarize(rows, {**SETUP, "providers": ["right", "wrong"]})

    assert [p for p, _ in scan.recommended_order(summary)] == ["right", "wrong"]

    only_wrong = scan.summarize([_row("wrong", "q0", "incorrect")], {**SETUP, "providers": ["wrong"]})
    assert [p for p, _ in scan.recommended_order(only_wrong)] == ["wrong"]
    assert "no order to suggest" not in scan.markdown(only_wrong)


def test_a_sealqa_scan_breaks_accuracy_down_by_its_own_labels_and_names_what_it_withheld(scan: ModuleType) -> None:
    labels = {"freshness": "fast-changing", "search_results": "conflicting"}
    rows = [_row("nimble", f"q{i}", "correct", withheld=1 if i == 0 else 0, **labels) for i in range(12)]
    setup = {**SETUP, "providers": ["nimble"], "questions": "sealqa-hard", "breakdowns": scan.SEALQA_BREAKDOWNS}
    summary = scan.summarize(rows, setup)

    assert summary["by_freshness"]["fast-changing"]["questions"] == 12
    assert summary["by_search_results"]["conflicting"]["leaders"] == ["nimble"]
    assert "by_answer_type" not in summary
    report = scan.markdown(summary)
    assert "SealQA Seal-Hard" in report and "## By what search returns" in report
    assert "`nimble` 1" in report, "results kept from the judge are named, not hidden"
    assert "on SealQA Seal-Hard, not the library's default" in report


def test_the_report_states_the_questions_loaded_not_the_limit_asked_for(scan: ModuleType) -> None:
    rows = [_row("nimble", f"q{i}", "correct") for i in range(12)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["nimble"], "limit": 300, "asked": 12})

    assert scan.markdown(summary).splitlines()[2].startswith("12 SimpleQA questions")


def test_a_scan_from_before_question_sets_combines_with_a_simpleqa_one(scan: ModuleType, tmp_path: Path) -> None:
    old = _scan(tmp_path / "old", ["nimble"])
    new = _scan(tmp_path / "new", ["brave"], questions="simpleqa")
    _, setup = scan.combine([old, new])

    assert setup["providers"] == ["nimble", "brave"]
    with pytest.raises(SystemExit, match="questions"):
        scan.combine([old, _scan(tmp_path / "seal", ["exa"], questions="sealqa-0")])


def test_your_own_csv_needs_its_path(scan: ModuleType) -> None:
    with pytest.raises(SystemExit, match="--dataset"):
        scan.load_questions(scan.parse_args(["--questions", "csv"]))


def test_your_own_csv_is_read_with_the_columns_you_name(scan: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "mine.csv"
    path.write_text("q,gold,topic\nWho?,Alice,People\n", encoding="utf-8")
    args = scan.parse_args(
        ["--questions", "csv", "--dataset", str(path), "--question-column", "q", "--answer-column", "gold"]
    )
    dataset = scan.load_questions(args)

    assert [(q.question, q.answer, q.metadata["topic"]) for q in dataset.questions] == [("Who?", "Alice", "People")]


def test_scans_of_two_different_csvs_do_not_combine(scan: ModuleType, tmp_path: Path) -> None:
    def identity(text: str) -> dict[str, Any]:
        path = tmp_path / f"{len(text)}.csv"
        path.write_text(text, encoding="utf-8")
        return scan.csv_identity(scan.parse_args(["--questions", "csv", "--dataset", str(path)]))

    mine, theirs = identity("problem,answer\nWho?,Alice\n"), identity("problem,answer\nWhere?,Paris\n")
    assert (
        mine["dataset_sha256"] != theirs["dataset_sha256"]
        and mine["question_column"] == scan.parse_args([]).question_column
    )
    a = _scan(tmp_path / "a", ["nimble"], questions="csv", **mine)
    b = _scan(tmp_path / "b", ["brave"], questions="csv", **theirs)
    with pytest.raises(SystemExit, match="dataset_sha256"):
        scan.combine([a, b])


def test_a_csv_that_repeats_a_question_is_refused_before_any_search(scan: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "mine.csv"
    path.write_text("problem,answer\nWho?,Alice\nWho?,Bob\n", encoding="utf-8")
    args = scan.parse_args(["--questions", "csv", "--dataset", str(path), "--providers", "nimble"])

    with pytest.raises(SystemExit, match="more than once"):
        asyncio.run(scan.run(args, {"providers": ["nimble"], "dataset": str(path), "breakdowns": []}))


def test_scans_that_withheld_different_pages_do_not_combine(scan: ModuleType, tmp_path: Path) -> None:
    old = _scan(tmp_path / "old", ["nimble"], questions="sealqa-hard")
    new = _scan(tmp_path / "new", ["brave"], questions="sealqa-hard", withheld=["sealqa", "2506.01062"])
    with pytest.raises(SystemExit, match="withheld"):
        scan.combine([old, new])
    again = _scan(tmp_path / "again", ["exa"], questions="sealqa-hard", withheld=["sealqa", "2506.01062"])
    assert scan.combine([new, again])[1]["providers"] == ["brave", "exa"]

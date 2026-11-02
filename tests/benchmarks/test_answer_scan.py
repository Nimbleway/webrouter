"""The answer scan's report, its rows and its baseline -- what it builds from answers, not the answers themselves."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from web_router.evals import Dataset, Question
from web_router.exceptions import ProviderError
from web_router.types import Citation
from web_router.types_answer import AnswerResponse

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "answer.py"


@pytest.fixture(scope="module")
def scan() -> Iterator[ModuleType]:
    # Imports the shared helpers as siblings, as it does when run from its folder.
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("answer_scan", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(SCRIPT.parent))
        for name in ("_common", "_questions", "_report_tables"):
            sys.modules.pop(name, None)


SETUP = {
    "limit": 12,
    "seed": 0,
    "judge": "openai:gpt-4o",
    "started": "2026-11-01T00:00:00+00:00",
    "asked": 12,
}


def _row(provider: str, question: str, grade: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "question": question,
        "target": "a",
        "latency": 1.0,
        "answer": "a",
        "grade": grade,
        "error": None,
        "failed_at": None,
        "citations": 3,
        "leaked": False,
        "cost_usd": None,
        "topic": "Science",
        "answer_type": "Date",
        **extra,
    }


def test_a_failed_and_a_leaked_answer_are_both_left_out_of_accuracy(scan: ModuleType) -> None:
    rows = [_row("perplexity", f"q{i}", "correct" if i < 9 else "incorrect") for i in range(10)]
    rows += [_row("perplexity", "q10", None, failed_at="answer", error="HTTP 500", answer=None, latency=None)]
    rows += [_row("perplexity", "q11", None, leaked=True)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["perplexity"]})
    score = summary["overall"]["perplexity"]

    assert (score["served"], score["correct"], score["failed"], score["leaked"]) == (10, 9, 1, 1)
    assert score["accuracy"] == 0.9
    report = scan.markdown(summary)
    assert "*Leaked* rows cite one of the set's own pages" in report


def test_only_a_stated_cost_is_shown_and_never_an_estimate(scan: ModuleType) -> None:
    rows = [_row("perplexity", f"q{i}", "correct", cost_usd=0.0015) for i in range(4)]
    rows += [_row("linkup", f"q{i}", "correct") for i in range(4)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["perplexity", "linkup"]})
    report = scan.markdown(summary)

    assert summary["overall"]["perplexity"]["stated_cost_mean"] == pytest.approx(0.0015)
    assert summary["overall"]["linkup"]["stated_cost_total"] is None
    assert "$0.0015 (4 stated)" in report
    assert "not stated" in report


def test_the_cost_and_tokens_a_backend_states_are_read_from_its_payload(scan: ModuleType) -> None:
    perplexity = AnswerResponse(
        provider="perplexity",
        query="q",
        answer="a",
        raw=[{"usage": {"input_tokens": 5257, "output_tokens": 29, "cost": {"total_cost": 0.00146}}}],
    )
    exa = AnswerResponse(provider="exa", query="q", answer="a", raw=[{"costDollars": {"total": 0.005}}])
    linkup = AnswerResponse(provider="linkup", query="q", answer="a", raw=[{"answer": "a", "sources": []}])

    assert scan.stated_cost(perplexity) == 0.00146
    assert scan.stated_tokens(perplexity) == {"input_tokens": 5257, "output_tokens": 29}
    assert scan.stated_cost(exa) == 0.005
    assert scan.stated_cost(linkup) is None
    assert scan.stated_tokens(linkup) == {}


class _Router:
    def __init__(self, response: AnswerResponse | Exception) -> None:
        self.response = response

    async def answer(self, question: str, *, provider: str) -> AnswerResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Judge:
    def __init__(self) -> None:
        self.graded: list[tuple[str, str, str]] = []

    async def agrade(self, question: str, target: str, predicted: str) -> str:
        self.graded.append((question, target, predicted))
        return "correct"


SEALQA = Dataset(
    name="sealqa",
    questions=[Question(question="q", answer="a")],
    withheld_markers=("sealqa",),
)


async def test_an_answer_citing_the_sets_own_page_is_leaked_and_never_graded(scan: ModuleType) -> None:
    response = AnswerResponse(
        provider="perplexity",
        query="q",
        answer="a",
        citations=[Citation(url="https://huggingface.co/datasets/vtllms/sealqa")],
    )
    judge = _Judge()

    row = await scan.answer_row(_Router(response), judge, SEALQA, "perplexity", "q", "a")

    assert row["leaked"] is True
    assert row["grade"] is None
    assert judge.graded == []


async def test_a_clean_answer_is_graded_as_the_backend_wrote_it(scan: ModuleType) -> None:
    response = AnswerResponse(
        provider="linkup", query="q", answer="Argentina", citations=[Citation(url="https://en.wikipedia.org/x")]
    )
    judge = _Judge()

    row = await scan.answer_row(_Router(response), judge, SEALQA, "linkup", "q", "a")

    assert (row["grade"], row["citations"], row["leaked"]) == ("correct", 1, False)
    assert judge.graded == [("q", "a", "Argentina")]


async def test_a_backend_failure_is_the_rows_and_is_never_graded(scan: ModuleType) -> None:
    judge = _Judge()

    row = await scan.answer_row(_Router(ProviderError("linkup", "HTTP 500")), judge, SEALQA, "linkup", "q", "a")

    assert row["failed_at"] == "answer"
    assert "HTTP 500" in row["error"]
    assert judge.graded == []


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


def test_the_baseline_stands_beside_on_the_same_questions_only(scan: ModuleType, tmp_path: Path) -> None:
    baseline = _write_search_scan(tmp_path / "search")
    rows = [_row("perplexity", f"q{i}", "correct" if i % 2 else "incorrect") for i in range(6)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["perplexity"], "baseline": str(baseline)})

    tavily = summary["baseline"]["overall"]["tavily"]
    assert (tavily["served"], tavily["correct"]) == (6, 6), "a question this scan did not ask is left out"
    report = scan.markdown(summary)
    assert "## Against search, with the judge writing the answer" in report
    assert "| search + judge | `tavily` | 100% (6/6)" in report
    assert "| answer() | `perplexity` | 50% (3/6)" in report


def test_a_baseline_graded_by_another_judge_is_refused(scan: ModuleType, tmp_path: Path) -> None:
    baseline = _write_search_scan(tmp_path / "search", judge="anthropic:claude")

    with pytest.raises(SystemExit, match="judge"):
        scan.summarize(
            [_row("perplexity", "q0", "correct")], {**SETUP, "providers": ["perplexity"], "baseline": str(baseline)}
        )


def test_answer_scans_graded_by_different_judges_do_not_combine(scan: ModuleType, tmp_path: Path) -> None:
    for name, judge in (("a", "openai:gpt-4o"), ("b", "anthropic:claude")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "run.json").write_text(json.dumps({**SETUP, "judge": judge, "providers": [name]}), encoding="utf-8")
        (folder / "rows.jsonl").write_text(json.dumps(_row(name, "q0", "correct")) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="judge"):
        scan.combine([tmp_path / "a", tmp_path / "b"])


def test_with_no_answer_backend_configured_the_scan_says_how_to_add_one(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan, "configured_answer_providers", list)
    args = scan.parse_args([])

    with pytest.raises(SystemExit, match=r"\.env\.example"):
        scan.setup_of(args)


class _BrokenJudge:
    closed = False

    async def agrade(self, question: str, target: str, predicted: str) -> str:
        raise RuntimeError("Error code: 401 - Your API key has expired.")

    async def aclose(self) -> None:
        self.closed = True


async def test_a_judge_that_cannot_grade_stops_the_scan_before_any_backend_is_asked(scan: ModuleType) -> None:
    judge = _BrokenJudge()

    with pytest.raises(SystemExit, match="no backend was asked: Error code: 401"):
        await scan.check_judge(judge)

    assert judge.closed


class _FailingJudge:
    async def agrade(self, question: str, target: str, predicted: str) -> str:
        raise RuntimeError("judge unavailable")


async def test_a_judge_failure_mid_scan_is_the_rows_and_keeps_the_answer(scan: ModuleType) -> None:
    response = AnswerResponse(provider="linkup", query="q", answer="Argentina", citations=[])

    row = await scan.answer_row(_Router(response), _FailingJudge(), SEALQA, "linkup", "q", "a")

    assert (row["failed_at"], row["answer"], row["grade"]) == ("judge", "Argentina", None)
    summary = scan.summarize([{**row, "topic": "Science", "answer_type": "Date"}], {**SETUP, "providers": ["linkup"]})
    assert summary["overall"]["linkup"]["failed"] == 1


async def test_only_the_cited_sources_decide_a_leak_when_the_answer_marks_them(scan: ModuleType) -> None:
    read_only = Citation(url="https://huggingface.co/datasets/vtllms/sealqa")
    cited = Citation(url="https://en.wikipedia.org/x", field="[2]")
    response = AnswerResponse(provider="linkup", query="q", answer="Argentina [2]", citations=[read_only, cited])

    row = await scan.answer_row(_Router(response), _Judge(), SEALQA, "linkup", "q", "a")

    assert row["leaked"] is False
    assert row["grade"] == "correct"


def test_citations_per_answer_counts_answered_rows_only(scan: ModuleType) -> None:
    rows = [_row("you", "q0", "correct", citations=2), _row("you", "q1", None, failed_at="answer", citations=0)]

    assert scan.summarize(rows, {**SETUP, "providers": ["you"]})["overall"]["you"]["citations_mean"] == 2


def test_a_repeated_backend_is_asked_once(scan: ModuleType) -> None:
    assert scan.setup_of(scan.parse_args(["--providers", "linkup,perplexity,linkup"]))["providers"] == [
        "linkup",
        "perplexity",
    ]


def test_a_mismatched_baseline_is_refused_before_anything_is_asked(scan: ModuleType, tmp_path: Path) -> None:
    baseline = _write_search_scan(tmp_path / "search")

    with pytest.raises(SystemExit, match="seed"):
        scan.setup_of(
            scan.parse_args(["--providers", "linkup", "--seed", "7", "--limit", "12", "--baseline", str(baseline)])
        )

"""The answer scan's rows, scores and report -- what it builds from answers, not the answers themselves."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import ANSWER_SETUP as SETUP
from tests.benchmarks.conftest import answer_scan_row as _row
from web_router.evals import Dataset, Question
from web_router.exceptions import ProviderError
from web_router.types import Citation
from web_router.types_answer import AnswerResponse


def test_a_failed_and_a_leaked_answer_are_both_left_out_of_accuracy(answer_scan: ModuleType) -> None:
    rows = [_row("perplexity", f"q{i}", "correct" if i < 9 else "incorrect") for i in range(10)]
    rows += [_row("perplexity", "q10", None, failed_at="answer", error="HTTP 500", answer=None, latency=None)]
    rows += [_row("perplexity", "q11", None, leaked=True)]
    summary = answer_scan.summarize(rows, {**SETUP, "providers": ["perplexity"]})
    score = summary["overall"]["perplexity"]

    assert (score["served"], score["correct"], score["failed"], score["leaked"]) == (10, 9, 1, 1)
    assert score["accuracy"] == 0.9
    report = answer_scan.markdown(summary)
    assert "*Leaked* rows cite one of the set's own pages" in report


def test_only_a_stated_cost_is_shown_and_never_an_estimate(answer_scan: ModuleType) -> None:
    rows = [_row("perplexity", f"q{i}", "correct", cost_usd=0.0015) for i in range(4)]
    rows += [_row("linkup", f"q{i}", "correct") for i in range(4)]
    summary = answer_scan.summarize(rows, {**SETUP, "providers": ["perplexity", "linkup"]})
    report = answer_scan.markdown(summary)

    assert summary["overall"]["perplexity"]["stated_cost_mean"] == pytest.approx(0.0015)
    assert summary["overall"]["linkup"]["stated_cost_total"] is None
    assert "$0.0015 (4 stated)" in report
    assert "not stated" in report


def test_the_cost_and_tokens_a_backend_states_are_read_from_its_payload(answer_scan: ModuleType) -> None:
    perplexity = AnswerResponse(
        provider="perplexity",
        query="q",
        answer="a",
        raw=[{"usage": {"input_tokens": 5257, "output_tokens": 29, "cost": {"total_cost": 0.00146}}}],
    )
    exa = AnswerResponse(provider="exa", query="q", answer="a", raw=[{"costDollars": {"total": 0.005}}])
    linkup = AnswerResponse(provider="linkup", query="q", answer="a", raw=[{"answer": "a", "sources": []}])

    assert answer_scan.stated_cost(perplexity) == 0.00146
    assert answer_scan.stated_tokens(perplexity) == {"input_tokens": 5257, "output_tokens": 29}
    assert answer_scan.stated_cost(exa) == 0.005
    assert answer_scan.stated_cost(linkup) is None
    assert answer_scan.stated_tokens(linkup) == {}


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


async def test_an_answer_citing_the_sets_own_page_is_leaked_and_never_graded(answer_scan: ModuleType) -> None:
    response = AnswerResponse(
        provider="perplexity",
        query="q",
        answer="a",
        citations=[Citation(url="https://huggingface.co/datasets/vtllms/sealqa")],
    )
    judge = _Judge()

    row = await answer_scan.answer_row(_Router(response), judge, SEALQA, "perplexity", "q", "a")

    assert row["leaked"] is True
    assert row["grade"] is None
    assert judge.graded == []


async def test_a_clean_answer_is_graded_as_the_backend_wrote_it(answer_scan: ModuleType) -> None:
    response = AnswerResponse(
        provider="linkup", query="q", answer="Argentina", citations=[Citation(url="https://en.wikipedia.org/x")]
    )
    judge = _Judge()

    row = await answer_scan.answer_row(_Router(response), judge, SEALQA, "linkup", "q", "a")

    assert (row["grade"], row["citations"], row["leaked"]) == ("correct", 1, False)
    assert judge.graded == [("q", "a", "Argentina")]


async def test_a_backend_failure_is_the_rows_and_is_never_graded(answer_scan: ModuleType) -> None:
    judge = _Judge()

    row = await answer_scan.answer_row(_Router(ProviderError("linkup", "HTTP 500")), judge, SEALQA, "linkup", "q", "a")

    assert row["failed_at"] == "answer"
    assert "HTTP 500" in row["error"]
    assert judge.graded == []


class _BrokenJudge:
    label = "openai:expired"
    closed = False

    def check(self) -> None:
        """The key is set; it is only on the first grade that it turns out to have expired."""

    async def agrade(self, question: str, target: str, predicted: str) -> str:
        raise RuntimeError("Error code: 401 - Your API key has expired.")

    async def aclose(self) -> None:
        type(self).closed = True


async def test_run_stops_on_a_dead_judge_before_any_answer(
    answer_scan: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def opened(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a router was opened, so a backend could be asked")

    monkeypatch.setattr(answer_scan, "OpenAIJudge", _BrokenJudge)
    monkeypatch.setattr(answer_scan, "AsyncWebRouter", opened)
    path = tmp_path / "q.csv"
    path.write_text("problem,answer\nWho?,Alice\n", encoding="utf-8")
    args = answer_scan.parse_args(["--questions", "csv", "--dataset", str(path), "--providers", "linkup"])

    with pytest.raises(SystemExit, match="no backend was asked: Error code: 401"):
        await answer_scan.run(args, answer_scan.setup_of(args), lambda rows: None)

    assert _BrokenJudge.closed


class _FailingJudge:
    async def agrade(self, question: str, target: str, predicted: str) -> str:
        raise RuntimeError("judge unavailable")


async def test_a_judge_failure_mid_scan_is_the_rows_and_keeps_the_answer(answer_scan: ModuleType) -> None:
    response = AnswerResponse(provider="linkup", query="q", answer="Argentina", citations=[])

    row = await answer_scan.answer_row(_Router(response), _FailingJudge(), SEALQA, "linkup", "q", "a")

    assert (row["failed_at"], row["answer"], row["grade"]) == ("judge", "Argentina", None)
    summary = answer_scan.summarize(
        [{**row, "topic": "Science", "answer_type": "Date"}], {**SETUP, "providers": ["linkup"]}
    )
    assert summary["overall"]["linkup"]["failed"] == 1


async def test_a_page_read_but_not_cited_is_a_leak_too(answer_scan: ModuleType) -> None:
    """Any page a backend returns decides a leak, whether or not it marks which ones it cites."""
    read_only = Citation(url="https://huggingface.co/datasets/vtllms/sealqa")
    cited = Citation(url="https://en.wikipedia.org/x", field="[2]")
    response = AnswerResponse(provider="linkup", query="q", answer="Argentina [2]", citations=[read_only, cited])

    row = await answer_scan.answer_row(_Router(response), _Judge(), SEALQA, "linkup", "q", "a")

    assert row["leaked"] is True
    assert row["grade"] is None


def test_citations_per_answer_counts_answered_rows_only(answer_scan: ModuleType) -> None:
    rows = [_row("you", "q0", "correct", citations=2), _row("you", "q1", None, failed_at="answer", citations=0)]

    assert answer_scan.summarize(rows, {**SETUP, "providers": ["you"]})["overall"]["you"]["citations_mean"] == 2

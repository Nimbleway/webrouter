"""Unlabeled question sets: questions with no gold, Nimble's two published sets, and `evaluate()` refusing them."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from tests.test_evals import FakeJudge, _Ledger, _member, _questions, _sealqa_parquet, registered
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_evals import (
    EVALS_QUESTION_SETS,
    PUBLIC_BENCHMARKS_COMMIT,
    PUBLIC_BENCHMARKS_SETS,
    PUBLIC_BENCHMARKS_URL,
)
from web_router.evals import Dataset, Question
from web_router.evals import datasets as datasets_module

__all__ = ["registered"]  # the fixture, imported for the tests below

HEADER = ["question", "category"]
ROWS = [
    ["Who are Ramp's customers?", "customers_and_market"],
    ["Who is Snowflake's CTO?", "leadership_and_ownership"],
]


def _published(path: Path, rows: list[list[str]] = ROWS, header: list[str] = HEADER) -> Path:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


# -- Questions with no gold --------------------------------------------------


def test_a_question_may_have_no_answer_but_never_a_blank_one() -> None:
    assert Question(question="Who?").answer is None
    with pytest.raises(ValueError, match="at least 1 character"):
        Question(question="Who?", answer="")


def test_rows_read_with_no_answer_column_are_unanswered_and_keep_the_rest_as_metadata() -> None:
    rows = [dict(zip(HEADER, row, strict=True)) for row in ROWS]

    dataset = Dataset.from_rows("mine", rows, question_column="question", answer_column=None)

    assert [question.answer for question in dataset.questions] == [None, None]
    assert dataset.questions[0].metadata == dict(zip(HEADER[1:], ROWS[0][1:], strict=True))


def test_a_named_answer_column_is_still_required_on_every_row(tmp_path: Path) -> None:
    mine = tmp_path / "mine.csv"
    mine.write_text("q,a\nWho?,Alice\nWhere?,\n", encoding="utf-8")

    with pytest.raises(ValueError, match="row 2 has nothing under 'a'"):
        Dataset.from_csv(mine, question_column="q", answer_column="a")
    unlabeled = Dataset.from_csv(mine, question_column="q", answer_column=None)
    assert [question.answer for question in unlabeled.questions] == [None, None]
    assert unlabeled.questions[0].metadata == {"a": "Alice"}


# -- Nimble's published sets -------------------------------------------------


@pytest.mark.parametrize("name", ["due-diligence", "lead-enrichment"])
def test_a_published_set_is_read_by_name_from_a_local_copy(tmp_path: Path, name: str) -> None:
    dataset = Dataset.for_set(name, path=_published(tmp_path / "questions.csv"))

    assert dataset.name == name and len(dataset) == 2
    assert all(question.answer is None and question.metadata == {} for question in dataset.questions)
    assert len(Dataset.for_set(name, path=tmp_path / "questions.csv", limit=1)) == 1


def test_a_published_set_reads_an_answer_column_as_gold_and_ignores_the_rest(tmp_path: Path) -> None:
    rows = [[question, category, "Alice"] for question, category in ROWS]
    copy = _published(tmp_path / "questions.csv", rows, ["question", "category", "answer"])

    dataset = Dataset.public_benchmarks("lead-enrichment", path=copy)

    assert [(question.answer, question.metadata) for question in dataset.questions] == [("Alice", {}), ("Alice", {})]


@pytest.mark.parametrize(
    ("name", "folder"), [("due-diligence", "Due-Diligence-Benchmark"), ("lead-enrichment", "Lead-Enrichment-Benchmark")]
)
def test_a_published_set_is_downloaded_from_its_folder_at_the_pinned_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, folder: str
) -> None:
    fetched: list[str] = []
    text = _published(tmp_path / "questions.csv").read_bytes()

    def fetch(url: str, *, follow_redirects: bool = True) -> bytes:
        fetched.append(url)
        return text

    monkeypatch.setattr(datasets_module, "_fetch", fetch)

    assert len(Dataset.public_benchmarks(name)) == 2
    assert fetched == [PUBLIC_BENCHMARKS_URL.format(commit=PUBLIC_BENCHMARKS_COMMIT, folder=folder)]
    assert fetched[0].endswith(f"/{folder}/data/questions.csv")


def test_the_published_sets_are_named_alongside_the_others() -> None:
    assert set(PUBLIC_BENCHMARKS_SETS) <= set(EVALS_QUESTION_SETS)
    with pytest.raises(ValueError, match="sealqa-hard, due-diligence, lead-enrichment, csv"):
        Dataset.for_set("mmlu")
    with pytest.raises(
        ValueError, match="no question set 'mmlu'; choose one of \\['due-diligence', 'lead-enrichment'\\]"
    ):
        Dataset.public_benchmarks("mmlu")


def test_a_question_set_is_loaded_by_name_and_a_csv_by_its_path_and_columns(tmp_path: Path) -> None:
    mine = tmp_path / "mine.csv"
    mine.write_text("q,gold,topic\nWho?,Alice,People\nWhere?,Paris,Places\n", encoding="utf-8")
    dataset = Dataset.for_set("csv", path=mine, question_column="q", answer_column="gold", limit=1, seed=3)
    seal = Dataset.for_set("sealqa-0", path=_sealqa_parquet(tmp_path / "seal-0.parquet"))

    assert len(dataset) == 1 and dataset.questions[0].question in {"Who?", "Where?"}
    assert seal.name == "sealqa-seal-0" and seal.withheld_markers
    with pytest.raises(ValueError, match="pass its path"):
        Dataset.for_set("csv")
    with pytest.raises(ValueError, match="no question set 'mmlu'"):
        Dataset.for_set("mmlu")


# -- evaluate() refuses what it cannot grade ---------------------------------


def _unlabeled() -> Dataset:
    return Dataset(name="dd", questions=[Question(question="Who?", answer="Alice"), Question(question="Where?")])


def test_evaluate_refuses_an_unanswered_question_before_any_search(registered: _Ledger) -> None:
    _member(registered, "first")

    with WebRouter("first") as router:
        with pytest.raises(ValueError, match="'dd' has 1 question\\(s\\) with none \\(the first: 'Where\\?'\\)"):
            router.evaluate(_unlabeled(), providers=["first"], judge=FakeJudge())
        assert router.get_provider("first").requests == []


async def test_the_async_evaluate_refuses_it_the_same_way(registered: _Ledger) -> None:
    _member(registered, "first")

    async with AsyncWebRouter("first") as router:
        with pytest.raises(ValueError, match="judge of your own"):
            await router.evaluate(_unlabeled(), providers=["first"], judge=FakeJudge())
        assert router.get_provider("first").requests == []


def test_a_labeled_set_is_graded_against_its_gold_as_before(registered: _Ledger) -> None:
    _member(registered, "first")

    with WebRouter("first") as router:
        report = router.evaluate(_questions(("Who?", "Alice")), providers=["first"], judge=FakeJudge({"Who?": "Alice"}))

    assert [(row.target, row.grade) for row in report.rows] == [("Alice", "correct")]

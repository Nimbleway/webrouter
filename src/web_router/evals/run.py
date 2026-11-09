"""The evaluation loop: every question through :meth:`~web_router.WebRouter.benchmark`, every answer through the judge.

For each question, one benchmark fan-out gives a row per provider; for each row
the backend served, the judge synthesizes an answer from its results and grades
it against the gold. The rows are then scored per provider. Nothing here talks
to a backend directly -- the search is the same public call a caller makes.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from web_router.constants import BENCHMARK_TIMEOUT, EVALS_CONCURRENCY, MSG_EVALS_EMPTY_DATASET, MSG_EVALS_JUDGE_FAILED
from web_router.evals.datasets import Dataset, Question
from web_router.evals.judge import Judge, OpenAIJudge
from web_router.evals.types import EvaluationReport, EvaluationRow, FailedAt, Grade, ProviderScore
from web_router.exceptions import ProviderError, describe
from web_router.types import Provider, SearchDepth, SearchResult
from web_router.types_benchmark import BenchmarkRun

if TYPE_CHECKING:
    from web_router.client import AsyncWebRouter, WebRouter
    from web_router.providers.base import SearchProvider


def evaluate(
    router: WebRouter,
    dataset: Dataset,
    *,
    providers: Sequence[str | Provider | SearchProvider] | None = None,
    judge: Judge | None = None,
    concurrency: int = EVALS_CONCURRENCY,
    timeout: float | None = BENCHMARK_TIMEOUT,
    max_results: int | None = None,
    search_depth: SearchDepth | None = None,
) -> EvaluationReport:
    """``dataset`` through ``providers`` on the sync router, every answer graded. See :meth:`WebRouter.evaluate`."""
    _check(dataset)
    # A judge built here is closed here; one passed in is the caller's to close.
    owned = OpenAIJudge() if judge is None else None
    judge = judge or owned
    assert judge is not None
    _ready(judge)

    def one(question: Question) -> list[EvaluationRow]:
        report = router.benchmark(
            question.question, providers=providers, timeout=timeout, max_results=max_results, search_depth=search_depth
        )
        return [_judged(judge, dataset, question, run) for run in report.runs]

    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            per_question = list(pool.map(one, dataset.questions))
    finally:
        if owned is not None:
            owned.close()
    return _report(dataset, judge, per_question)


async def aevaluate(
    router: AsyncWebRouter,
    dataset: Dataset,
    *,
    providers: Sequence[str | Provider | SearchProvider] | None = None,
    judge: Judge | None = None,
    concurrency: int = EVALS_CONCURRENCY,
    timeout: float | None = BENCHMARK_TIMEOUT,
    max_results: int | None = None,
    search_depth: SearchDepth | None = None,
) -> EvaluationReport:
    """The async twin of :func:`evaluate`. See :meth:`AsyncWebRouter.evaluate`."""
    _check(dataset)
    owned = OpenAIJudge() if judge is None else None
    judge = judge or owned
    assert judge is not None
    _ready(judge)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(question: Question) -> list[EvaluationRow]:
        async with gate:
            report = await router.benchmark(
                question.question,
                providers=providers,
                timeout=timeout,
                max_results=max_results,
                search_depth=search_depth,
            )
            return list(await asyncio.gather(*(_ajudged(judge, dataset, question, run) for run in report.runs)))

    try:
        per_question = list(await asyncio.gather(*(one(question) for question in dataset.questions)))
    finally:
        if owned is not None:
            await owned.aclose()
    return _report(dataset, judge, per_question)


def _check(dataset: Dataset) -> None:
    if not dataset.questions:
        raise ValueError(MSG_EVALS_EMPTY_DATASET.format(name=dataset.name))


def _ready(judge: Judge) -> None:
    # Before the first search is paid for: a judge with no SDK or no key would
    # otherwise fail every row one at a time, after every search had run.
    judge.check()


def _judged(judge: Judge, dataset: Dataset, question: Question, run: BenchmarkRun) -> EvaluationRow:
    if run.response is None:
        return _row(question, run, error=run.error, failed_at="search")
    results, withheld = _visible(dataset, run.response.results)
    try:
        answer = judge.synthesize(question.question, results)
    except Exception as exc:
        return _row(question, run, withheld, error=_judge_error(judge, "synthesis", exc), failed_at="synthesis")
    try:
        grade = judge.grade(question.question, question.answer, answer)
    except Exception as exc:
        return _row(
            question, run, withheld, answer=answer, error=_judge_error(judge, "grading", exc), failed_at="grading"
        )
    return _row(question, run, withheld, answer=answer, grade=grade)


async def _ajudged(judge: Judge, dataset: Dataset, question: Question, run: BenchmarkRun) -> EvaluationRow:
    if run.response is None:
        return _row(question, run, error=run.error, failed_at="search")
    results, withheld = _visible(dataset, run.response.results)
    try:
        answer = await judge.asynthesize(question.question, results)
    except Exception as exc:
        return _row(question, run, withheld, error=_judge_error(judge, "synthesis", exc), failed_at="synthesis")
    try:
        grade = await judge.agrade(question.question, question.answer, answer)
    except Exception as exc:
        return _row(
            question, run, withheld, answer=answer, error=_judge_error(judge, "grading", exc), failed_at="grading"
        )
    return _row(question, run, withheld, answer=answer, grade=grade)


def _visible(dataset: Dataset, results: list[SearchResult]) -> tuple[list[SearchResult], int]:
    # A result from a page that may hold the dataset's own answers would grade
    # the backend on finding the answer key, not the answer.
    kept = [result for result in results if not dataset.withholds(result.url)]
    return kept, len(results) - len(kept)


def _row(
    question: Question,
    run: BenchmarkRun,
    withheld: int = 0,
    *,
    answer: str | None = None,
    grade: Grade | None = None,
    error: Exception | None = None,
    failed_at: FailedAt | None = None,
) -> EvaluationRow:
    return EvaluationRow(
        provider=run.provider,
        question=question.question,
        target=question.answer,
        latency=run.latency,
        withheld=withheld,
        answer=answer,
        grade=grade,
        error=error,
        failed_at=failed_at,
    )


def _judge_error(judge: Judge, step: FailedAt, exc: Exception) -> ProviderError:
    # The judge's failure, wrapped under the judge's name: it is not the backend's.
    return ProviderError(judge.name, MSG_EVALS_JUDGE_FAILED.format(step=step, error=describe(exc)), original=exc)


def _report(dataset: Dataset, judge: Judge, per_question: list[list[EvaluationRow]]) -> EvaluationReport:
    rows = [row for rows in per_question for row in rows]
    order: list[str] = []
    for row in rows:
        if row.provider not in order:
            order.append(row.provider)
    return EvaluationReport(
        dataset=dataset.name, judge=judge.label, rows=rows, scores=[_score(name, rows) for name in order]
    )


def _score(provider: str, rows: list[EvaluationRow]) -> ProviderScore:
    mine = [row for row in rows if row.provider == provider]
    served = [row for row in mine if row.served]
    correct = sum(1 for row in served if row.grade == "correct")
    incorrect = sum(1 for row in served if row.grade == "incorrect")
    attempted = correct + incorrect
    latencies = sorted(row.latency for row in served)
    return ProviderScore(
        provider=provider,
        questions=len(mine),
        served=len(served),
        failed=len(mine) - len(served),
        correct=correct,
        incorrect=incorrect,
        not_attempted=len(served) - attempted,
        accuracy=correct / len(served) if served else None,
        accuracy_when_attempted=correct / attempted if attempted else None,
        failure_rate=(len(mine) - len(served)) / len(mine) if mine else 0.0,
        latency_p50=_percentile(latencies, 0.5),
        latency_p95=_percentile(latencies, 0.95),
    )


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile, so every reported latency is one that was measured."""
    if not sorted_values:
        return None
    index = max(0, min(len(sorted_values) - 1, math.ceil(fraction * len(sorted_values)) - 1))
    return sorted_values[index]


__all__ = ["aevaluate", "evaluate"]

"""Accuracy on top of :meth:`~web_router.WebRouter.benchmark`: questions with gold answers, judged by an LLM.

:meth:`~web_router.WebRouter.evaluate` runs a :class:`Dataset` -- the SimpleQA
test set, or a CSV of your own -- through several providers, has a
:class:`Judge` synthesize one answer from each provider's results and grade it
against the gold, and reports a score per provider. The judge is a model, and
the report names it: accuracy here is that model's judgment of that
backend's retrieval, comparable with figures the same judge produced.
"""

from web_router.evals.datasets import Dataset, Question
from web_router.evals.judge import AnthropicJudge, Judge, OpenAIJudge
from web_router.evals.run import aevaluate, evaluate
from web_router.evals.types import EvaluationReport, EvaluationRow, FailedAt, Grade, ProviderScore

__all__ = [
    "AnthropicJudge",
    "Dataset",
    "EvaluationReport",
    "EvaluationRow",
    "FailedAt",
    "Grade",
    "Judge",
    "OpenAIJudge",
    "ProviderScore",
    "Question",
    "aevaluate",
    "evaluate",
]

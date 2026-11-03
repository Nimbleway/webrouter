"""What the question scans share: the question sets, loading them, and the setup and arguments that name them.

``run.py`` scans search backends and ``answer.py`` answer backends on the same
question sets, so a question is loaded, checked, labelled and recorded the same
way in both. Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from web_router.constants import EVALS_CSV_SET
from web_router.evals import Dataset

DEFAULT_LIMIT = 300
SEALQA_BREAKDOWNS = [["topic", "By topic"], ["freshness", "By freshness"], ["search_results", "By what search returns"]]
"""SealQA's labels: how fast the answer changes, and whether search turns up conflicting or unhelpful pages."""
QUESTION_SETS: dict[str, dict[str, Any]] = {
    "simpleqa": {
        "dataset": "SimpleQA (openai/simple-evals)",
        "short": "SimpleQA",
        "breakdowns": [["topic", "By topic"], ["answer_type", "By answer type"]],
    },
    "sealqa-0": {
        "dataset": "SealQA Seal-0 (vtllms/sealqa, pinned)",
        "short": "SealQA Seal-0",
        "breakdowns": SEALQA_BREAKDOWNS,
    },
    "sealqa-hard": {
        "dataset": "SealQA Seal-Hard (vtllms/sealqa, pinned)",
        "short": "SealQA Seal-Hard",
        "breakdowns": SEALQA_BREAKDOWNS,
    },
    "csv": {"dataset": "a CSV of your own", "short": "your questions", "breakdowns": [["topic", "By topic"]]},
}
"""``--questions``: what the report calls the set, and the labels it breaks accuracy down by."""
MSG_REPEATED_QUESTION = (
    "{dataset} asks {question!r} more than once; a scan labels each row by its question text, so each must be unique"
)
MSG_OTHER_QUESTIONS = "{folder} asked different questions from the first scan; only scans of the same questions combine"
MSG_CANNOT_COMBINE = (
    "{folder} was a different run ({key}: {theirs!r}, not {ours!r}); only scans of the same questions and judge combine"
)


def question_args(parser: argparse.ArgumentParser) -> None:
    """The arguments that choose the questions: the set, how many, the seed, and a local file's columns."""
    parser.add_argument(
        "--questions", choices=sorted(QUESTION_SETS), default="simpleqa", help="the question set (default: simpleqa)"
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="questions to sample")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--dataset", type=Path, default=None, help="a local copy of the set's file (required for --questions csv)"
    )
    parser.add_argument("--question-column", default="problem", help="--questions csv: the question column")
    parser.add_argument("--answer-column", default="answer", help="--questions csv: the gold answer column")


def csv_identity(args: argparse.Namespace) -> dict[str, Any]:
    """A CSV run's file digest and columns, for ``run.json``; nothing for a named set."""
    if args.questions != EVALS_CSV_SET or args.dataset is None:
        return {}
    return Dataset.csv_identity(args.dataset, args.question_column, args.answer_column)


def base_setup(args: argparse.Namespace, providers: list[str]) -> dict[str, Any]:
    """The part of ``run.json`` every question scan writes: the set, its sample and the backends asked."""
    chosen = QUESTION_SETS[args.questions]
    return {
        "questions": args.questions,
        "dataset": chosen["dataset"] if args.questions != EVALS_CSV_SET else str(args.dataset),
        "breakdowns": chosen["breakdowns"],
        **csv_identity(args),
        "limit": args.limit,
        "seed": args.seed,
        "providers": providers,
    }


def load_questions(args: argparse.Namespace) -> Dataset:
    """``--questions``, sampled by ``--seed``: SimpleQA, a SealQA split, or a CSV of your own."""
    try:
        return Dataset.for_set(
            args.questions,
            path=args.dataset,
            question_column=args.question_column,
            answer_column=args.answer_column,
            limit=args.limit,
            seed=args.seed,
        )
    except ValueError as exc:
        raise SystemExit(f"--questions {args.questions}: {exc} A local file goes in --dataset.") from exc


def load_checked_questions(args: argparse.Namespace, setup: dict[str, Any]) -> Dataset:
    """:func:`load_questions`, refused when a question repeats, with what was asked and withheld added to ``setup``."""
    dataset = load_questions(args)
    texts = [q.question for q in dataset.questions]
    if len(set(texts)) != len(texts):
        repeated = next(text for text in texts if texts.count(text) > 1)
        raise SystemExit(MSG_REPEATED_QUESTION.format(dataset=setup["dataset"], question=repeated))
    setup["asked"] = len(dataset)
    if dataset.withheld_markers:
        # What was kept from the judge: scans that withheld different pages are not judged alike.
        setup["withheld"] = list(dataset.withheld_markers)
    return dataset


def question_tags(metadata: dict[str, Any]) -> dict[str, Any]:
    """A question's labels. SimpleQA's own column is named ``metadata``; a local CSV may hold anything there."""
    inner = metadata.get("metadata", metadata)
    return inner if isinstance(inner, dict) else {}


def breakdowns(setup: dict[str, Any]) -> list[list[str]]:
    """The labels a scan's report breaks accuracy down by. A scan from before ``--questions`` existed was SimpleQA."""
    return setup.get("breakdowns") or QUESTION_SETS["simpleqa"]["breakdowns"]

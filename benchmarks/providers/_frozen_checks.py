"""How the frozen results name a question and class a row's error, and the checks ``frozen.py`` runs on scans.

``build`` refuses scans that are not one set: other questions, other gold
answers, another setup, a row twice or a row missing. ``verify`` refuses a
manifest whose failed-row counts are not the rows'. ``build`` and ``join``
refuse a dataset that does not hold every question, or whose gold answers
changed. Each check raises ``SystemExit`` naming the set and the first fault,
so nothing is written past it. ``frozen.py`` and ``_frozen_mixed.py`` share
the row primitives here: the grades, the error classes and a scan's reader.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

QUESTION_ID_CHARS = 16

MSG_UNRESOLVED = "{set}: {count} question_id(s) do not resolve in {questions}, e.g. {example}"
MSG_ID_COLLISION = "{questions}: two different questions share question_id {question_id}"
MSG_NO_GOLD = "{set}: {run} has a row with no gold answer (`target`) for question {question_id}; the scan is damaged"
MSG_TWO_GOLDS = "{set}: {run} graded question {question_id} against two gold answers; the scan is damaged"
MSG_ANSWERS_CHANGED = (
    "{set}: the gold answers in {questions} no longer match the ones the scans were graded on;"
    " the dataset changed upstream"
)
MSG_OTHER_QUESTIONS = "{set}: {run} asked different questions from {first}; only scans of the same questions combine"
MSG_OTHER_SETUP = "{set}: {run} differs on {key} ({theirs!r}, not {ours!r}); only scans of the same setup combine"
MSG_OTHER_ANSWERS = (
    "{set}: {run} has another gold answer for question {question_id} than {first};"
    " only scans graded on the same answers combine"
)
MSG_REPEATED_ROW = "{set}: {provider} ({mode}) has question {question_id} more than once"
MSG_NO_ROWS = "{set}: {provider} was asked in the scans but has no row; the scan stopped before it ran"
MSG_MISSING_ROWS = (
    "{set}: {provider} ({mode}) has no row for {count} of {total} questions, e.g. {question_id};"
    " was the scan cut short?"
)
MSG_FAILED_ROWS = (
    "{set}: manifest.json names {stated} failed rows per backend, but the rows hold {counted};"
    " rebuild the folder rather than editing it"
)

GRADES = ("correct", "incorrect", "not_attempted")
DEFAULT_MODE = "default"
"""A row's ``mode`` when the scan asked for none: the backend's own default for that method."""
LATENCY_DIGITS = 3

RUN_DEFAULTS = {"questions": "simpleqa"}
"""A ``run.json`` from before ``--questions`` existed asked SimpleQA."""

RATE_LIMIT_ERROR, QUOTA_ERROR = "rate_limit", "quota"
RATE_LIMIT_OR_QUOTA_ERRORS = frozenset({RATE_LIMIT_ERROR, QUOTA_ERROR})
"""Error classes of a call that never reached the question: route-eval leaves them out of accuracy."""
ERROR_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("timeout", re.compile(r"timed? ?out|timeout|deadline", re.IGNORECASE)),
    (RATE_LIMIT_ERROR, re.compile(r"rate.?limit|too many requests|\bHTTP 429\b", re.IGNORECASE)),
    (QUOTA_ERROR, re.compile(r"\bHTTP 402\b|quota|credits?\b|payment required", re.IGNORECASE)),
    ("invalid_request", re.compile(r"takes a query of at most|does not support|UnsupportedParameter", re.IGNORECASE)),
    ("http_4xx", re.compile(r"\bHTTP 4\d\d\b")),
    ("http_5xx", re.compile(r"\bHTTP 5\d\d\b")),
)
"""A backend's error message, by the first pattern it matches; anything else is ``other``.

The message itself is never kept: a vendor's text can quote an account, a key
prefix or a request id.
"""
OTHER_ERROR = "other"
JUDGE_ERROR = "judge"
JUDGE_STAGES = frozenset({"judge", "synthesis", "grading"})
"""``failed_at`` values that mean the judge failed, not the backend: the answer scan's and the search scan's."""
LEAKED_ERROR = "leaked"
BUDGET_ERROR = "budget"
"""A row whose backend answered but whose grading the run's budget refused (``tools_effort.py``): kept, ungraded."""
ERROR_NAMES = (*(name for name, _ in ERROR_CLASSES), OTHER_ERROR, JUDGE_ERROR, LEAKED_ERROR, BUDGET_ERROR)

Scan = tuple[str, dict[str, Any], dict[str, str]]
"""One scan of a set: its folder name, its setup and its gold answers by question id."""


def normalise_question(text: str) -> str:
    """A question's text as its id is computed: Unicode NFKC, whitespace collapsed to one space, ends stripped."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def question_id(text: str, urls: Iterable[str] = ()) -> str:
    """A stable id for a question: the first 16 hex characters of the SHA-256 of its normalised text.

    A question asked with the pages to read gets its own id: its text and its
    URLs, one space apart, are hashed together.
    """
    text = " ".join((text, *urls))
    return hashlib.sha256(normalise_question(text).encode("utf-8")).hexdigest()[:QUESTION_ID_CHARS]


def answers_digest(golds: Mapping[str, str]) -> str:
    """SHA-256 of the gold answers by question id, so ``join`` can tell when a dataset changed under them."""
    lines = "".join(f"{qid}\t{normalise_question(gold)}\n" for qid, gold in sorted(golds.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def question_index(questions: Iterable[Any], name: str) -> dict[str, Any]:
    """question_id -> the dataset's :class:`~web_router.evals.Question`; refuses two questions on one id."""
    index: dict[str, Any] = {}
    for question in questions:
        qid = question_id(question.question)
        if qid in index and (
            normalise_question(index[qid].question) != normalise_question(question.question)
            or normalise_question(str(index[qid].answer)) != normalise_question(str(question.answer))
        ):
            raise SystemExit(MSG_ID_COLLISION.format(questions=name, question_id=qid))
        index[qid] = question
    return index


def require_resolved(set_name: str, rows: Iterable[Mapping[str, Any]], index: Mapping[str, Any], questions: str) -> int:
    """How many question ids ``rows`` holds, or ``SystemExit`` naming one the dataset does not hold."""
    ids = {row["question_id"] for row in rows}
    missing = sorted(ids - set(index))
    if missing:
        raise SystemExit(
            MSG_UNRESOLVED.format(set=set_name, count=len(missing), questions=questions, example=missing[0])
        )
    return len(ids)


def require_same_answers(
    set_name: str, entry: Mapping[str, Any], rows: Iterable[Mapping[str, Any]], index: Mapping[str, Any]
) -> None:
    """``SystemExit`` when the dataset's gold answers for the set's questions are not the ones its scans used."""
    golds = {row["question_id"]: str(index[row["question_id"]].answer) for row in rows}
    if answers_digest(golds) != entry["answers_sha256"]:
        raise SystemExit(MSG_ANSWERS_CHANGED.format(set=set_name, questions=entry["questions"]))


def read_golds(set_name: str, run: str, source: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """A scan's gold answers by question id, or ``SystemExit`` on a row with none."""
    golds: dict[str, Any] = {}
    for r in source:
        qid = question_id(str(r["question"]))
        if golds.setdefault(qid, r.get("target")) != r.get("target"):
            raise SystemExit(MSG_TWO_GOLDS.format(set=set_name, run=run, question_id=qid))
    blank = sorted(qid for qid, gold in golds.items() if gold in (None, ""))
    if blank:
        raise SystemExit(MSG_NO_GOLD.format(set=set_name, run=run, question_id=blank[0]))
    return golds


def check_same_questions(set_name: str, scan: Scan, first: Scan) -> None:
    """``SystemExit`` when a scan asked other questions than the set's first."""
    if set(scan[2]) != set(first[2]):
        raise SystemExit(MSG_OTHER_QUESTIONS.format(set=set_name, run=scan[0], first=first[0]))


def check_same_answers(set_name: str, scan: Scan, first: Scan) -> None:
    """``SystemExit`` when a scan graded a question on another gold answer than the set's first."""
    changed = sorted(qid for qid, gold in scan[2].items() if gold != first[2][qid])
    if changed:
        raise SystemExit(MSG_OTHER_ANSWERS.format(set=set_name, run=scan[0], question_id=changed[0], first=first[0]))


def check_same_setup(set_name: str, scan: Scan, first: Scan, keys: Iterable[str]) -> None:
    """``SystemExit`` when a scan's setup differs from the set's first on a key ``--combine`` requires."""
    ours, theirs = first[1], scan[1]
    differ = [key for key in keys if theirs.get(key) != ours.get(key)]
    if differ:
        key = differ[0]
        raise SystemExit(
            MSG_OTHER_SETUP.format(set=set_name, run=scan[0], key=key, theirs=theirs.get(key), ours=ours.get(key))
        )


def check_no_repeats(set_name: str, rows: Iterable[Mapping[str, Any]]) -> None:
    """``SystemExit`` when a backend has a row for one question twice, by the same method."""
    seen: set[tuple[str, str, str, str]] = set()
    for key in ((row["provider"], row["mode"], row["question_id"], row.get("method", "")) for row in rows):
        if key in seen:
            raise SystemExit(MSG_REPEATED_ROW.format(set=set_name, provider=key[0], mode=key[1], question_id=key[2]))
        seen.add(key)


def check_complete(
    set_name: str, rows: Iterable[Mapping[str, Any]], question_ids: set[str], asked: Iterable[str] = ()
) -> None:
    """``SystemExit`` when a backend the scans asked has no row, or no row for some of the set's questions."""
    by_backend: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        by_backend.setdefault((row["provider"], row["mode"]), set()).add(row["question_id"])
    for provider in sorted(set(asked) - {provider for provider, _ in by_backend}):
        raise SystemExit(MSG_NO_ROWS.format(set=set_name, provider=provider))
    for (provider, mode), missing in sorted((key, sorted(question_ids - ids)) for key, ids in by_backend.items()):
        if missing:
            raise SystemExit(
                MSG_MISSING_ROWS.format(
                    set=set_name,
                    provider=provider,
                    mode=mode,
                    count=len(missing),
                    total=len(question_ids),
                    question_id=missing[0],
                )
            )


def failed_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """How many rows failed (``error`` set) for each backend, as provider -> mode -> count; a backend with none is 0."""
    counts: dict[str, dict[str, int]] = {}
    for row in sorted(rows, key=lambda r: (r["provider"], r["mode"])):
        by_mode = counts.setdefault(row["provider"], {})
        by_mode[row["mode"]] = by_mode.get(row["mode"], 0) + (row["error"] is not None)
    return counts


def check_failed_rows(set_name: str, entry: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> None:
    """``SystemExit`` when a set's manifest entry counts other failed rows per backend than its rows hold."""
    counted = failed_rows(rows)
    if entry.get("failed_rows") != counted:
        raise SystemExit(MSG_FAILED_ROWS.format(set=set_name, stated=entry.get("failed_rows"), counted=counted))


def error_class(row: Mapping[str, Any]) -> str | None:
    """What went wrong on a row, as a class rather than the vendor's message; ``None`` when nothing did."""
    if row.get("leaked"):
        return LEAKED_ERROR
    message = row.get("error")
    if not message:
        return None
    if row.get("failed_at") == BUDGET_ERROR:
        return BUDGET_ERROR
    if row.get("failed_at") in JUDGE_STAGES:
        return JUDGE_ERROR
    return next((name for name, pattern in ERROR_CLASSES if pattern.search(str(message))), OTHER_ERROR)


def measures(row: Mapping[str, Any]) -> dict[str, Any]:
    """A run row's grade, latency, cost and error class, cleaned the same way for every frozen set."""
    latency, cost = row.get("latency"), row.get("cost_usd")
    return {
        "grade": row.get("grade") if row.get("grade") in GRADES else None,
        "latency_s": None if latency is None else round(float(latency), LATENCY_DIGITS),
        "cost_usd": None if cost is None else float(cost),
        "error": error_class(row),
    }


def read_run(folder: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A scan folder's ``run.json``, with :data:`RUN_DEFAULTS` under it, and its rows."""
    setup = {**RUN_DEFAULTS, **json.loads((folder / "run.json").read_text(encoding="utf-8"))}
    rows = [json.loads(line) for line in (folder / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return setup, rows

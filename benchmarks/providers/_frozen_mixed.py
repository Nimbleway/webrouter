"""A ``mixed`` frozen set: one made by ``tools_effort.py``, whose rows call several methods and name their own.

Kept apart from ``frozen.py``, which dispatches to it from ``freeze_set`` and
``set_index``. Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from _frozen_checks import (
    check_no_repeats,
    failed_rows,
    measures,
    question_id,
    read_run,
)
from _tools_effort_plan import QUESTIONS as MIXED_QUESTIONS
from _tools_effort_plan import REUSED_ANSWER_SET, expected_keys

MIXED = "mixed"
"""A set whose rows call several methods (``tools_effort.py``'s): each row names its own, and its run.json its sample.

Such a set is added to ``FROZEN_SETS`` as ``{"questions": "tools-effort", "method": "mixed", "runs": [folder]}``,
after the set it reuses answer rows from.
"""
MSG_MIXED_UNPLANNED = (
    "{set}: {count} rows are not in the run's plan, e.g. {example}; only the planned (question, method, backend,"
    " mode) rows are frozen"
)
MSG_MIXED_INCOMPLETE = (
    "{set}: {count} planned rows are missing, e.g. {example}; was the run cut short? Run it again to finish it"
)


def mixed_row(set_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """One row of a mixed run as a frozen row: it names its question id, method and mode itself."""
    return {
        "set": set_name,
        "question_id": row["question_id"],
        "provider": row["provider"],
        "method": row["path"],
        "mode": row["mode"],
        **measures(row),
    }


def reused_row(set_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """An answer row copied into a mixed set, its cost filled as the set's own: stated, else the list price."""
    # Imported here so verify and the tests of the other sets need no price list.
    from web_router import prices

    cost = row["cost_usd"]
    if cost is None and row["error"] is None:
        cost = prices.estimate(row["provider"], row["method"])
    return {**row, "set": set_name, "cost_usd": cost}


def freeze_mixed(
    set_name: str, spec: Mapping[str, Any], results: Path, built: Mapping[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A mixed set's frozen rows and manifest entry: its run's rows, and the answer rows it reuses from ``built``.

    Refuses a row twice, a row its sample does not plan, and a run that has not fetched every row it plans.
    """
    (run,) = spec["runs"]
    setup, source = read_run(results / run)
    expected = expected_keys(setup["sample"])
    rows = [mixed_row(set_name, r) for r in source]
    rows += [
        reused_row(set_name, r)
        for r in built[REUSED_ANSWER_SET]
        if (r["question_id"], r["method"], r["provider"], r["mode"]) in expected
    ]
    check_no_repeats(set_name, rows)
    found = {(r["question_id"], r["method"], r["provider"], r["mode"]) for r in rows}
    unplanned = sorted(found - expected)
    if unplanned:
        raise SystemExit(MSG_MIXED_UNPLANNED.format(set=set_name, count=len(unplanned), example=unplanned[0]))
    missing = sorted(expected - found)
    if missing:
        raise SystemExit(MSG_MIXED_INCOMPLETE.format(set=set_name, count=len(missing), example=missing[0]))
    rows.sort(key=lambda r: (r["question_id"], r["method"], r["provider"], r["mode"]))
    entry = {
        "questions": MIXED_QUESTIONS,
        "dataset": setup.get("dataset"),
        "method": MIXED,
        "limit": len(setup["sample"]),
        "seed": None,
        "max_results": setup.get("max_results"),
        "judge": setup.get("judge"),
        "question_count": len(setup["sample"]),
        "answers_sha256": setup["answers_sha256"],
        "row_count": len(rows),
        "providers": sorted({r["provider"] for r in rows}),
        "failed_rows": failed_rows(rows),
        "runs": [{"run": run, "started": setup.get("started"), "rows": len(source), "reused": REUSED_ANSWER_SET}],
        "cost_usd": "stated by the response where it states one, else the list price in web_router.prices",
        "sample": setup["sample"],
    }
    return rows, entry


def sample_index(sample: list[Mapping[str, Any]], local: Mapping[str, Path]) -> dict[str, Any]:
    """A mixed set's questions by id, each found at its row in the public set it came from, with its pages."""
    from web_router.evals import Dataset, Question

    full = {name: Dataset.for_set(name, path=local.get(name)) for name in sorted({e["source"] for e in sample})}
    index: dict[str, Any] = {}
    for entry in sample:
        found = full[entry["source"]].questions
        question = found[entry["index"]] if entry["index"] < len(found) else None
        urls = entry.get("urls") or []
        if question is None or question_id(question.question, urls) != entry["question_id"]:
            continue  # left out, so require_resolved names it
        labels = {"group": entry["group"], "source": entry["source"], "urls": urls}
        index[entry["question_id"]] = Question(question=question.question, answer=question.answer, metadata=labels)
    return index

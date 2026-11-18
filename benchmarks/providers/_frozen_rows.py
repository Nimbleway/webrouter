"""One scan row as a frozen row, a set's manifest entry, and a frozen row with its question put back.

Kept apart from ``frozen.py``, which re-exports every name here. Imported as a
sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from _common import DEPTH_MARK, split_label
from _frozen_checks import DEFAULT_MODE, Scan, answers_digest, failed_rows, measures, question_id
from _tools_effort_plan import QUESTIONS as MIXED_QUESTIONS

LABELS = {
    "simpleqa": ("topic", "answer_type"),
    "sealqa-hard": ("topic", "freshness", "search_results"),
    MIXED_QUESTIONS: ("group", "source", "urls"),
}
"""The labels ``join`` adds from each dataset: the breakdowns the scan reports used."""


def split_setup(setup: str) -> tuple[str, str]:
    """A scan's backend label as ``(provider, mode)``: ``tavily:ultra-fast@deep`` is ``tavily``, ``ultra-fast@deep``."""
    provider, vendor_mode, depth = split_label(setup)
    mode = vendor_mode + (DEPTH_MARK + depth if depth else "")
    return provider, mode or DEFAULT_MODE


def frozen_row(set_name: str, method: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """One scan row as a frozen row: ids, grade, latency, stated cost and error class, nothing written in words."""
    provider, mode = split_setup(str(row["provider"]))
    return {
        "set": set_name,
        "question_id": question_id(str(row["question"])),
        "provider": provider,
        "method": method,
        "mode": mode,
        **measures(row),
    }


def manifest_entry(spec: Mapping[str, Any], first: Scan, rows: list[dict[str, Any]], runs: list[Any]) -> dict[str, Any]:
    """A set's entry in ``manifest.json``: its setup, sizes, backends, their failed rows and the scans it is made of."""
    setup = first[1]
    return {
        "questions": spec["questions"],
        "dataset": setup.get("dataset"),
        "method": spec["method"],
        "limit": setup.get("limit"),
        "seed": setup.get("seed"),
        "max_results": setup.get("max_results"),
        "judge": setup.get("judge"),
        "question_count": len(first[2]),
        "answers_sha256": answers_digest(first[2]),
        "row_count": len(rows),
        "providers": sorted({r["provider"] for r in rows}),
        "failed_rows": failed_rows(rows),
        "runs": runs,
    }


def joined_row(row: Mapping[str, Any], question: Any, labels: Iterable[str]) -> dict[str, Any]:
    """A frozen row with its question, gold answer and the dataset's labels for it put back."""
    meta = question.metadata.get("metadata", question.metadata)
    meta = meta if isinstance(meta, dict) else {}
    return {**row, "question": question.question, "gold_answer": question.answer, **{k: meta.get(k) for k in labels}}

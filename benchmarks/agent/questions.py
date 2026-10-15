"""The decision-gate question sets: one pool of rows by id, and named sets that list ids.

Two sources make the pool:

- ``questions/written.jsonl``, the rows we wrote, in the dataset-spec v0 shape plus a gold ``answer``.
- Seal-Hard's stable rows (``freshness`` never- or slow-changing), built from the pinned SealQA download through
  :meth:`Dataset.sealqa` and never copied into the repo. Each id is ``sealqa-hard:`` and the first 10 hex of the
  SHA-1 of the question.

``questions/sets.json`` names the sets (``"*"`` is every Seal-Hard stable row) and holds the label overrides for
SealQA rows a set uses in its own way. Asking for several sets gives each question once.

    uv run python benchmarks/agent/questions.py tools,scoped,seal-hard-stable
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from web_router.evals.datasets import Dataset, Question

HERE = Path(__file__).parent / "questions"
WRITTEN = HERE / "written.jsonl"
SETS = HERE / "sets.json"
SEALQA_SPLIT = "seal-hard"
ID_PREFIX = "sealqa-hard:"
STABLE_FRESHNESS = ("never-changing", "slow-changing")
ALL_SEALQA = "*"


def sealqa_id(question: str) -> str:
    return ID_PREFIX + hashlib.sha1(question.encode()).hexdigest()[:10]


def _sealqa_row(question: Question) -> dict[str, Any]:
    return {
        "id": sealqa_id(question.question),
        "prompt": question.question,
        "answer": question.answer,
        "asked_on": None,
        "kind": "clear",
        "topic": f"{SEALQA_SPLIT}:{question.metadata['freshness']}",
        "labels": {"well_scoped": True, "tool_suggestion": "search", "recent": None, "coverage": None},
        "source": "sealqa",
    }


def build_pool(
    written: Iterable[dict[str, Any]], sealqa: Iterable[Question], overrides: dict[str, dict[str, str]]
) -> dict[str, dict[str, Any]]:
    """Every row by id, in the shape the agent benchmarks read: ``question`` and ``tool_tag`` beside the spec's."""
    pool: dict[str, dict[str, Any]] = {}
    stable = (q for q in sealqa if q.metadata.get("freshness") in STABLE_FRESHNESS)
    for row in [*written, *map(_sealqa_row, stable)]:
        row = dict(row)
        override = overrides.get(row["id"], {})
        row["topic"] = override.get("topic", row["topic"])
        tool = override.get("tool_tag") or row["labels"].get("tool_suggestion")
        row["labels"] = {**row["labels"], "tool_suggestion": tool}
        row["question"], row["tool_tag"] = row["prompt"], tool or "none"
        pool[row["id"]] = row
    return pool


def set_ids(name: str, sets: dict[str, Any], pool: dict[str, dict[str, Any]]) -> list[str]:
    if name not in sets:
        raise SystemExit(f"unknown question set {name!r}; known: {', '.join(sorted(sets))}")
    ids = sets[name]
    return [i for i, row in pool.items() if row["source"] == "sealqa"] if ids == ALL_SEALQA else list(ids)


def resolve(names: Iterable[str], sets: dict[str, Any], pool: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows of the named sets, in order, each question once."""
    ids = [i for name in names for i in set_ids(name, sets, pool)]
    return [pool[i] for i in dict.fromkeys(ids)]


def load_set(names: str) -> list[dict[str, Any]]:
    """``names`` is one set or several, comma-separated. Prints how many unique questions they hold."""
    config = json.loads(SETS.read_text())
    written = [json.loads(line) for line in WRITTEN.read_text().splitlines() if line.strip()]
    pool = build_pool(written, Dataset.sealqa(SEALQA_SPLIT).questions, config["overrides"])
    wanted = names.split(",")
    rows = resolve(wanted, config["sets"], pool)
    listed = sum(len(set_ids(name, config["sets"], pool)) for name in wanted)
    print(f"questions {names}: {len(rows)} unique ({listed - len(rows)} duplicates dropped)", flush=True)
    return rows


if __name__ == "__main__":
    load_set(sys.argv[1] if len(sys.argv) > 1 else "tools")

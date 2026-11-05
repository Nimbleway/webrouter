"""route-eval's data: a frozen set read as questions, each with every candidate backend's row.

Apart from ``route_eval.py`` so the ranking and the routers can read a set
without importing the replay that imports them. Imported as a sibling, by
name, as ``_common`` is.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frozen import FROZEN_DIR, SUFFIX, read_rows

TWIN_SETS = ("sealqa-hard-254", "sealqa-hard-254-twin")
"""The same questions run twice: their spread is the noise floor."""
DEFAULT_MODE = "default"

MSG_UNKNOWN_SET = "{name}: no frozen set by that name (there are: {known})"
MSG_NO_JOINED = "{path} not found: run frozen.py join first, or leave --joined out"


@dataclass(frozen=True)
class Question:
    """One question of a set: its id, its text when joined, and each candidate backend's frozen row."""

    question_id: str
    prompt: str | None
    rows: Mapping[str, Mapping[str, Any]]

    @property
    def candidates(self) -> list[str]:
        return sorted(self.rows)


def load_set(name: str, folder: Path = FROZEN_DIR, joined: Path | None = None) -> tuple[str, list[Question]]:
    """A frozen set's method and questions; each question's text from ``joined`` when given, else ``None``.

    Only rows in the backend's default mode take part: v1 ranks backends, not modes.
    """
    path = folder / (name + SUFFIX)
    if not path.is_file():
        known = sorted(p.name.removesuffix(SUFFIX) for p in folder.glob("*" + SUFFIX))
        raise SystemExit(MSG_UNKNOWN_SET.format(name=name, known=", ".join(known)))
    prompts: dict[str, str] = {}
    if joined is not None:
        joined_file = joined / (name + ".jsonl")
        if not joined_file.is_file():
            raise SystemExit(MSG_NO_JOINED.format(path=joined_file))
        for line in joined_file.read_text(encoding="utf-8").splitlines():
            if line:
                row = json.loads(line)
                prompts[row["question_id"]] = row["question"]
    by_question: dict[str, dict[str, dict[str, Any]]] = {}
    methods = set()
    for row in read_rows(path):
        if row["mode"] != DEFAULT_MODE:
            continue
        methods.add(row["method"])
        by_question.setdefault(row["question_id"], {})[row["provider"]] = row
    (method,) = methods
    questions = [Question(qid, prompts.get(qid), rows) for qid, rows in sorted(by_question.items())]
    return method, questions

"""What the benchmark scans' tests share: the extract and answer scans loaded from their folder, and a row of each."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

EXTRACT_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "extract.py"
ANSWER_SCRIPT = EXTRACT_SCRIPT.with_name("answer.py")
ANSWER_SIBLINGS = ("_common", "_questions", "_report_tables", "answer_report")
"""What the answer scan imports from its folder, under plain names, so they are forgotten once it is."""
EXTRACT_SIBLINGS = ("_common", "cli", "extract_report", "urls")
"""What the extract scan imports from its folder, under plain names, so they are forgotten once it is."""
FROZEN_SCRIPT = EXTRACT_SCRIPT.with_name("frozen.py")
FROZEN_SIBLINGS = ("_common", "_frozen_checks", "_tools_effort_plan")
"""What ``frozen.py`` imports from its folder, under plain names, so they are forgotten once it is."""
EXTRACT_SETUP = {
    "urls": "general",
    "urls_title": "the general mix",
    "urls_sha256": "abc",
    "url_column": "url",
    "category_column": "category",
    "limit": 12,
    "seed": 0,
    "format": None,
    "timeout": 120.0,
    "started": "2026-10-13T00:00:00+00:00",
}
"""A ``run.json`` as ``setup_of()`` writes it, minus the backends, which each test names."""


@pytest.fixture(scope="module")
def extract_scan() -> Iterator[ModuleType]:
    # The scan imports what it shares with its neighbour as a sibling, as it does run from its folder.
    sys.path.insert(0, str(EXTRACT_SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("extract_scan", EXTRACT_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(EXTRACT_SCRIPT.parent))
        for name in EXTRACT_SIBLINGS:
            sys.modules.pop(name, None)


def extract_row(
    provider: str,
    url: str,
    chars: int | None,
    category: str = "News / Media",
    error_type: str | None = "ProviderError",
) -> dict[str, Any]:
    """A measured row: ``chars`` is the text that came back, ``None`` a call that did not answer."""
    returned = chars is not None
    return {
        "url": url,
        "category": category,
        "provider": provider,
        "returned": returned,
        "with_text": bool(chars),
        "chars": chars or 0,
        "format": "markdown" if returned else None,
        "status_code": 200 if returned else None,
        "latency": 1.0,
        "error": None if returned else "HTTP 500",
        "error_type": None if returned else error_type,
    }


ANSWER_SETUP = {
    "limit": 12,
    "seed": 0,
    "judge": "openai:gpt-4o",
    "started": "2026-11-01T00:00:00+00:00",
    "asked": 12,
}
"""An answer scan's ``run.json``, minus the backends, which each test names."""


@pytest.fixture(scope="module")
def answer_scan() -> Iterator[ModuleType]:
    # Imports the shared helpers as siblings, as it does when run from its folder.
    sys.path.insert(0, str(ANSWER_SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("answer_scan", ANSWER_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(ANSWER_SCRIPT.parent))
        for name in ANSWER_SIBLINGS:
            sys.modules.pop(name, None)


def answer_scan_row(provider: str, question: str, grade: str | None, **extra: Any) -> dict[str, Any]:
    """One backend's graded answer to one question, as ``rows.jsonl`` holds it."""
    return {
        "provider": provider,
        "question": question,
        "target": "a",
        "latency": 1.0,
        "answer": "a",
        "grade": grade,
        "error": None,
        "failed_at": None,
        "citations": 3,
        "leaked": False,
        "cost_usd": None,
        "topic": "Science",
        "answer_type": "Date",
        **extra,
    }


TOOLS_EFFORT_SCRIPT = EXTRACT_SCRIPT.with_name("tools_effort.py")
TOOLS_EFFORT_SIBLINGS = (
    "_common",
    "_frozen_checks",
    "_tools_effort_plan",
    "_questions",
    "_report_tables",
    "answer_report",
)
"""What the tool and effort runner imports from its folder, under plain names, so they are forgotten once it is."""


@pytest.fixture(scope="module")
def tools_effort() -> Iterator[ModuleType]:
    # Imports the plan, the checks and the cost reader as siblings, as it does when run from its folder.
    sys.path.insert(0, str(TOOLS_EFFORT_SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("tools_effort", TOOLS_EFFORT_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered while it loads: its dataclasses look their module up by name.
        sys.modules["tools_effort"] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(TOOLS_EFFORT_SCRIPT.parent))
        for name in ("tools_effort", *TOOLS_EFFORT_SIBLINGS):
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def frozen() -> Iterator[ModuleType]:
    # Imports the combine keys and its checks as siblings, as it does when run from its folder.
    sys.path.insert(0, str(FROZEN_SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("frozen_results", FROZEN_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(FROZEN_SCRIPT.parent))
        for name in FROZEN_SIBLINGS:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def frozen_checks(frozen: ModuleType) -> ModuleType:
    """The checks ``frozen.py`` runs, as it imported them."""
    return sys.modules["_frozen_checks"]


def frozen_scan(
    folder: Path, providers: list[str], questions: list[str], gold: str = "secret gold {q}", **setup: Any
) -> None:
    """A finished search scan's folder, every backend graded correct on every question."""
    folder.mkdir(parents=True)
    run = {"questions": "simpleqa", "limit": 2, "seed": 0, "max_results": 5, "judge": "j", "providers": providers}
    (folder / "run.json").write_text(json.dumps({**run, **setup}), encoding="utf-8")
    rows = [
        {
            "provider": p,
            "question": q,
            "target": gold.format(q=q),
            "answer": "secret answer",
            "latency": 1.23456,
            "grade": "correct",
            "error": None,
            "failed_at": None,
        }
        for q in questions
        for p in providers
    ]
    (folder / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def one_frozen_set(frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, runs: list[str]) -> None:
    """``FROZEN_SETS`` as the one search set ``tiny``, made of the scan folders ``runs``."""
    sets = {"tiny": {"questions": "simpleqa", "method": "search", "runs": runs}}
    monkeypatch.setattr(frozen, "FROZEN_SETS", sets)

"""What the benchmark scans' tests share: the extract and answer scans loaded from their folder, and a row of each."""

from __future__ import annotations

import gzip
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts.benchmark_loader import FROZEN_SCRIPT, FROZEN_SIBLINGS, load_script

EXTRACT_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "extract.py"
ANSWER_SCRIPT = EXTRACT_SCRIPT.with_name("answer.py")
ANSWER_SIBLINGS = ("_common", "_questions", "_report_tables", "answer_report")
"""What the answer scan imports from its folder, under plain names, so they are forgotten once it is."""
EXTRACT_SIBLINGS = ("_common", "cli", "extract_report", "urls")
"""What the extract scan imports from its folder, under plain names, so they are forgotten once it is."""
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
    with load_script(EXTRACT_SCRIPT, "extract_scan", EXTRACT_SIBLINGS) as module:
        yield module


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
    with load_script(ANSWER_SCRIPT, "answer_scan", ANSWER_SIBLINGS) as module:
        yield module


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


@pytest.fixture(scope="module")
def frozen() -> Iterator[ModuleType]:
    with load_script(FROZEN_SCRIPT, "frozen_results", FROZEN_SIBLINGS) as module:
        yield module


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


ROUTE_EVAL_SCRIPT = EXTRACT_SCRIPT.with_name("route_eval.py")
ROUTE_EVAL_SIBLINGS = (
    "_common",
    "_report_tables",
    "frozen",
    "_frozen_checks",
    "_frozen_mixed",
    "_tools_effort_plan",
    "route_eval_paths",
    "route_eval_sets",
    "route_eval_rank",
    "route_eval_merge",
    "route_eval_routers",
    "route_eval_report",
    "route_eval_cli",
)
"""What route-eval imports from its folder, under plain names, so they are forgotten once it is."""


@pytest.fixture(scope="module")
def route_eval() -> Iterator[ModuleType]:
    with load_script(ROUTE_EVAL_SCRIPT, "route_eval_under_test", ROUTE_EVAL_SIBLINGS) as module:
        yield module


@pytest.fixture(scope="module")
def route_eval_rank(route_eval: ModuleType) -> ModuleType:
    """The ranking, as route-eval imported it."""
    return sys.modules["route_eval_rank"]


@pytest.fixture(scope="module")
def route_eval_floor(route_eval: ModuleType) -> ModuleType:
    """The noise floors, as route-eval imported them."""
    return sys.modules["route_eval_floor"]


@pytest.fixture(scope="module")
def route_eval_report(route_eval: ModuleType) -> ModuleType:
    """The report, as route-eval imported it."""
    return sys.modules["route_eval_report"]


def route_row(provider: str, grade: str | None, latency: float = 1.0, cost: float | None = None, **extra: Any) -> dict:
    """One frozen row of the set ``tiny``: ``qid``, ``method``, ``mode`` and ``error`` default to ``q``, search,
    default and none."""
    return {
        "set": "tiny",
        "question_id": extra.get("qid", "q"),
        "provider": provider,
        "method": extra.get("method", "search"),
        "mode": extra.get("mode", "default"),
        "grade": grade,
        "latency_s": latency,
        "cost_usd": cost,
        "error": extra.get("error"),
    }


def write_frozen_set(folder: Path, name: str, rows: list[dict[str, Any]]) -> None:
    """``rows`` as the frozen set ``name`` in ``folder``, one gzipped JSON line each."""
    folder.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({**r, "set": name}) + "\n" for r in rows).encode()
    (folder / f"{name}.jsonl.gz").write_bytes(gzip.compress(body))


def route_question(module: ModuleType, *rows: dict[str, Any], prompt: str | None = None) -> Any:
    """A question made of ``rows``, built with ``module``'s ``Question``."""
    return module.Question(rows[0]["question_id"], prompt, {r["provider"]: r for r in rows})


def route_stats(accuracy: float, cost: float | None, latency: float = 1.0) -> dict[str, Any]:
    """What the ranking reads of one backend."""
    return {"accuracy": accuracy, "cost_usd": cost, "latency_p50": latency}


SCAN_SCRIPT = EXTRACT_SCRIPT.with_name("run.py")
SCAN_SIBLINGS = (
    "_common",
    "_questions",
    "_report_tables",
    "_recommended",
    "route_eval_paths",
    "route_eval_sets",
    "route_eval_rank",
    "frozen",
    "_frozen_checks",
    "_frozen_mixed",
    "_tools_effort_plan",
)
"""What the search scan imports from its folder, under plain names, so they are forgotten once it is."""


@pytest.fixture(scope="module")
def scan() -> Iterator[ModuleType]:
    # Loaded under its own name: the agent benchmark's script is also called run.py.
    with load_script(SCAN_SCRIPT, "provider_scan", SCAN_SIBLINGS) as module:
        yield module


def scan_row(provider: str, question: str, grade: str | None, topic: str = "Science", **extra: Any) -> dict[str, Any]:
    """One backend's graded search on one question, as ``rows.jsonl`` holds it.

    ``grade=None`` is a failed search, a server error a ranking counts as a miss; ``error=`` gives another.
    """
    failed = grade is None
    return {
        "provider": provider,
        "question": question,
        "target": "a",
        "latency": 1.0,
        "answer": None if failed else "a",
        "grade": grade,
        "error": "HTTP 500" if failed else None,
        "failed_at": "search" if failed else None,
        "topic": topic,
        "answer_type": "Date",
        **extra,
    }


SCAN_SETUP = {
    "limit": 12,
    "seed": 0,
    "max_results": 5,
    "judge": "openai:gpt-4o",
    "started": "2026-09-30T00:00:00+00:00",
}
"""A search scan's ``run.json``, minus the backends, which each test names."""


def path_row(qid: str, path: str, grade: str | None, **extra: Any) -> dict[str, Any]:
    method, provider, *mode = path.split(":")
    return route_row(provider, grade, qid=qid, method=method, mode=mode[0] if mode else "default", **extra)


def _perplexity_low(qid: str, i: int) -> dict[str, Any]:
    """A `rate_limit` error on the first 15 questions, timed out on two, right on the last three."""
    if i < 15:
        return path_row(qid, "research:perplexity:low", None, latency=None, error="rate_limit")
    if i < 17:
        return path_row(qid, "research:perplexity:low", None, latency=None, error="timeout")
    return path_row(qid, "research:perplexity:low", "correct", cost=0.002)


def write_mixed_set(folder: Path) -> None:
    """20 questions; ``research:parallel:high`` right on all, ``extract:nimble`` asked on the first four only."""
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows += [
            path_row(qid, "search+extract:nimble:top3", "correct" if i < 8 else "incorrect", cost=0.008),
            path_row(qid, "search+extract:perplexity:top3", "incorrect", cost=0.007),
            path_row(qid, "answer:perplexity", "correct" if i < 12 else "incorrect", cost=0.002),
            path_row(qid, "research:parallel:low", "correct" if i < 16 else "incorrect", cost=0.005),
            path_row(qid, "research:parallel:high", "correct", cost=0.1, latency=30.0),
            _perplexity_low(qid, i),
        ]
        if i < 4:
            rows.append(path_row(qid, "extract:nimble", "correct", cost=0.001))
    write_frozen_set(folder, "mixed-tiny", rows)

"""The tool and effort set's run folder and plan: resuming a run, and the plan ``--dry-run`` prints.

Kept apart from ``tools_effort.py`` so the runner's other parts stay small.
Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _frozen_checks import answers_digest
from _tools_effort_budget import UNPRICED, backend_calls, call_cost, judge_calls, task_cost
from _tools_effort_plan import (
    DATASET,
    EASY,
    EXTRACT,
    FRAMES,
    HARD,
    MAX_RESULTS,
    PAGES_READ,
    PATHS,
    QUESTIONS,
    READER,
    SEALQA_HARD,
    SEARCH_EXTRACT,
    SET_NAME,
    SIMPLEQA,
    SIZES,
    SYNTHESIS,
    URL_GIVEN,
    URL_GIVEN_SIZES,
    Item,
    Task,
    build_sample,
    plan,
)
from _tools_effort_runner import PAGE_CHARS

from web_router.constants import (
    ENV_BRAVE_ANSWER_API_KEY,
    ENV_EXA_API_KEY,
    ENV_LINKUP_API_KEY,
    ENV_NIMBLE_API_KEY,
    ENV_OPENAI_API_KEY,
    ENV_PARALLEL_API_KEY,
    ENV_PERPLEXITY_API_KEY,
    ENV_YOU_API_KEY,
)
from web_router.evals import Dataset

ROWS_FILE, SPEND_FILE, RUN_FILE = "rows.jsonl", "spend.jsonl", "run.json"

# The run folder


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def done_keys(rows: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, str, str]]:
    """The rows a resumed run skips: every one written, failed or not -- a failed call may have billed."""
    return {(r["question_id"], r["path"], r["provider"], r["mode"]) for r in rows}


def order(tasks: Iterable[Task]) -> list[Task]:
    """Path by path, and within research the cheapest effort first, so a run stopped by its budget has whole columns."""
    return sorted(tasks, key=lambda t: (PATHS.index(t.path), task_cost(t).expected))


REQUIRED_KEYS = {
    "nimble": ENV_NIMBLE_API_KEY,
    "perplexity": ENV_PERPLEXITY_API_KEY,
    "linkup": ENV_LINKUP_API_KEY,
    "parallel": ENV_PARALLEL_API_KEY,
    "you": ENV_YOU_API_KEY,
    "exa": ENV_EXA_API_KEY,
    "brave": ENV_BRAVE_ANSWER_API_KEY,
}
"""The key each backend in the plan reads; Brave's answer key is its own."""


def missing_keys(tasks: Iterable[Task]) -> list[str]:
    needed = {REQUIRED_KEYS[t.provider] for t in tasks} | {ENV_OPENAI_API_KEY}
    if any(t.path in (SEARCH_EXTRACT, EXTRACT) for t in tasks):
        needed.add(REQUIRED_KEYS[READER])
    return sorted(name for name in needed if not os.environ.get(name))


def load_sample(local: Mapping[str, Path]) -> list[Item]:
    """The set's questions: the public sets downloaded, or read from the local copies ``--dataset`` names."""
    full = {name: Dataset.for_set(name, path=local.get(name)) for name in (SIMPLEQA, SEALQA_HARD, FRAMES)}
    return build_sample(full)


def setup_of(items: list[Item], judge: str, args: argparse.Namespace) -> dict[str, Any]:
    """``run.json``: the sample by id, never by text, and every setting a resumed run must share."""
    return {
        "set": SET_NAME,
        "questions": QUESTIONS,
        "dataset": DATASET,
        "judge": judge,
        "max_results": MAX_RESULTS,
        "pages_read": PAGES_READ,
        "page_chars": PAGE_CHARS,
        "research_timeout": args.research_timeout,
        "answers_sha256": answers_digest({i.qid: i.answer for i in items}),
        "sample": [i.entry() for i in items],
    }


MSG_OTHER_RUN = "{path} is another run ({key} differs); a resumed run must ask the same questions the same way"
RESUME_KEYS = (
    "set",
    "judge",
    "max_results",
    "pages_read",
    "page_chars",
    "research_timeout",
    "answers_sha256",
    "sample",
)


def open_run(out: Path, setup: dict[str, Any]) -> dict[str, Any]:
    """``run.json`` written for a new run, or checked against the one a resumed run left."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / RUN_FILE
    if path.is_file():
        before = json.loads(path.read_text(encoding="utf-8"))
        for key in RESUME_KEYS:
            if before.get(key) != setup.get(key):
                raise SystemExit(MSG_OTHER_RUN.format(path=path, key=key))
        setup = {**before, "resumed": [*before.get("resumed", []), _now()]}
    else:
        setup = {**setup, "started": _now()}
    path.write_text(json.dumps(setup, indent=2) + "\n", encoding="utf-8")
    return setup


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _appender(path: Path) -> Callable[[dict[str, Any]], None]:
    def append(record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return append


# The plan, without calling anything


def placeholder_sample() -> list[Item]:
    """The sample's shape with no question in it: every group at its size, each FRAMES question with all its pages.

    What ``--dry-run`` plans on, so it needs no download; a real FRAMES
    question has two to five links, so the extract count is an upper bound.
    """
    groups = ((EASY, SIMPLEQA), (HARD, SEALQA_HARD), (SYNTHESIS, FRAMES))
    items = [Item(group, source, i, qid=f"{group}-{i}") for group, source in groups for i in range(SIZES[group])]
    for source, count in URL_GIVEN_SIZES.items():
        urls = ("https://example.org/",) * (1 if source == SIMPLEQA else 5)
        items += [Item(URL_GIVEN, source, i, urls=urls, qid=f"{source}-url-{i}") for i in range(count)]
    return items


def dry_run(items: list[Item], budget: float) -> str:
    """The plan as text: questions per group, rows and calls per path, backend and mode, and the cost of each."""
    tasks = order(plan(items))
    groups = Counter((i.group, i.source) for i in items)
    lines = [f"{SET_NAME}: {len(items)} questions, {len(tasks)} rows (nothing is called)", ""]
    lines += ["Questions per group:"]
    lines += [f"  {group:<10} {source:<12} {count:>3}" for (group, source), count in groups.items()]
    columns: dict[tuple[str, str, str], list[Task]] = {}
    for task in tasks:
        columns.setdefault((task.path, task.provider, task.mode), []).append(task)
    lines += ["", f"  {'path':<15} {'backend':<11} {'mode':<8} {'rows':>4} {'calls':>5} {'worst':>9} {'expected':>9}"]
    totals = {"worst": 0.0, "expected": 0.0, "backend_worst": 0.0, "backend_expected": 0.0}
    calls = Counter[str]()
    for (path, provider, mode), column in columns.items():
        n_calls = sum(len(backend_calls(t)) for t in column)
        costs = [task_cost(t) for t in column]
        backend = [[call_cost(*c) for c in backend_calls(t)] for t in column]
        worst, expected = sum(c.worst for c in costs), sum(c.expected for c in costs)
        totals["worst"] += worst
        totals["expected"] += expected
        totals["backend_worst"] += sum(c.worst for cs in backend for c in cs)
        totals["backend_expected"] += sum(c.expected for cs in backend for c in cs)
        calls.update(method for t in column for _, method, _ in backend_calls(t))
        calls["judge"] += sum(len(judge_calls(t)) for t in column)
        lines.append(
            f"  {path:<15} {provider:<11} {mode:<8} {len(column):>4} {n_calls:>5} ${worst:>8.2f} ${expected:>8.2f}"
        )
    judge_worst_total = totals["worst"] - totals["backend_worst"]
    judge_expected = totals["expected"] - totals["backend_expected"]
    lines += [
        "",
        "Calls by method: " + ", ".join(f"{name} {count}" for name, count in calls.items()),
        "Unpriced calls, charged their stated fallback (never $0): "
        + ", ".join(f"{p} {m}{'' if mode is None else ':' + mode} ${c.worst}" for (p, m, mode), c in UNPRICED.items()),
        f"Backends: worst ${totals['backend_worst']:.2f}, expected ${totals['backend_expected']:.2f}",
        f"Judge (gpt-4o): worst ${judge_worst_total:.2f}, expected ${judge_expected:.2f}",
        f"Total: worst ${totals['worst']:.2f}, expected ${totals['expected']:.2f}; budget ${budget:.2f}",
        "The reused answer rows (the easy questions' answers from answer-simpleqa-300) cost nothing.",
    ]
    if totals["worst"] > budget:
        lines.append("Worst case is over the budget: the run reserves each row's worst case and stops before the cap.")
    return "\n".join(lines)

"""What the question scans' reports share: a graded group's numbers, and the table of them by question label.

``run.py`` and ``answer.py`` grade answers the same way, so both score a group
of rows and break accuracy down by label alike. Imported as a sibling, by
name, as ``_common`` is.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from _common import MIN_GROUP, _p, _pct, _wilson


def grade_stats(rows: list[dict[str, Any]], latencies: list[float] | None = None) -> dict[str, Any]:
    """Accuracy over the rows the judge graded, with its interval, and the latency over ``latencies``.

    ``latencies`` defaults to the graded rows' own.
    """
    served = [r for r in rows if r["grade"] is not None]
    correct = sum(r["grade"] == "correct" for r in served)
    incorrect = sum(r["grade"] == "incorrect" for r in served)
    if latencies is None:
        latencies = [r["latency"] for r in served]
    return {
        "questions": len(rows),
        "served": len(served),
        "correct": correct,
        "accuracy": correct / len(served) if served else None,
        "accuracy_ci95": _wilson(correct, len(served)),
        "accuracy_when_attempted": correct / (correct + incorrect) if correct + incorrect else None,
        "latency_p50": statistics.median(latencies) if latencies else None,
        "latency_p95": _pct(latencies, 0.95),
    }


def correct_of_all(score: Mapping[str, Any]) -> float:
    """Correct answers out of every question asked, a failed row counting as a miss."""
    return score["correct"] / score["questions"] if score["questions"] else 0.0


def search_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A search backend's numbers: :func:`grade_stats`, the searches that failed, and the results withheld."""
    stats = grade_stats(rows)
    return {**stats, "failed": len(rows) - stats["served"], "withheld": sum(r.get("withheld", 0) for r in rows)}


def breakdown(rows: list[dict[str, Any]], key: str, providers: list[str]) -> dict[str, dict[str, Any]]:
    """Accuracy per backend in each group of one label, and which backends lead the group."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[key] or "untagged"].append(row)
    table = {}
    for group in sorted(groups, key=lambda g: -len(groups[g])):
        members = groups[group]
        scores = {p: grade_stats([r for r in members if r["provider"] == p]) for p in providers}
        questions = len({r["question"] for r in members})
        accuracies = {p: s["accuracy"] for p, s in scores.items() if s["accuracy"] is not None}
        # Ranked on correct answers out of the group's questions, so a backend that
        # failed most of them cannot lead on the few it served.
        most = max((s["correct"] for s in scores.values()), default=0)
        leaders = [p for p, s in scores.items() if s["correct"] == most and most] if questions >= MIN_GROUP else []
        table[group] = {"questions": questions, "accuracy": accuracies, "leaders": leaders}
    return table


def breakdown_tables(summary: dict[str, Any], labels: list[list[str]], providers: list[str], intro: str) -> list[str]:
    """One report section per label: ``intro``, then each group's accuracy per backend, the leaders in bold."""
    out: list[str] = []
    for label, title in labels:
        out.append(f"## {title}\n")
        out.append(intro)
        out.append("| Group | Questions | " + " | ".join(f"`{p}`" for p in providers) + " |")
        out.append("|---|---|" + "---|" * len(providers))
        for group, entry in summary[f"by_{label}"].items():
            cells = []
            for p in providers:
                cell = _p(entry["accuracy"].get(p))
                cells.append(f"**{cell}**" if p in entry["leaders"] else cell)
            out.append(f"| {group} | {entry['questions']} | " + " | ".join(cells) + " |")
        out.append("")
    return out

"""route-eval's report on tool choice: each router's cost-weighted confusion matrix, and where routers disagree.

The numbers are :mod:`route_eval_matrix`'s and :mod:`route_eval_compare`'s;
:mod:`route_eval_report` appends these sections to each set's. Imported as a
sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from _common import _p

NO_CELL = "-"
MATRIX_INTRO = (
    "### Tool confusion, held out\n\n"
    "Each router's held-out questions by the tool its choice ran (rows) against the best tool (columns): the "
    "tool of the oracle's path, the one that got the question right, cheapest then fastest, or *none right*. "
    "A cell reads *questions (right), overspend, time over*: what the router's choices cost and took beyond "
    "the oracle's on those questions, as the replay charged them; a cheaper or faster choice adds nothing, and "
    "pays in accuracy instead. An extra with either side unknown is left out and counted unknown.\n\n"
    "A question's right tools are every tool with a path that got it right. **Wrong tool, wrong answer** counts "
    "the questions some tool got right and the router's did not: the accuracy its tool choice lost. **Right, "
    "not the cheapest tool** counts those it got right on another tool than the best, and its cost regret is "
    "their overspend over the cheapest right path. A wrong answer from a right tool on another backend is "
    "neither. Accuracy lost is the oracle's correct answers minus the router's, from every cause. The oracle "
    "is one frozen sample per path, an upper bound: a path right once by chance makes its tool a right one.\n"
)
COMPARE_INTRO = (
    "### Where routers disagree, held out\n\n"
    "Each router against `static` and every `--against` router, on the same held-out questions: how often the "
    "path that served differs (another backend of the same tool counts), how often the tool does, and on "
    "those questions which of the two alone was right, with the exact McNemar p-value of those two counts. "
    "Each accuracy carries its Wilson 95% interval. Mean cost is over the questions where both costs are "
    "known; the rest are counted once. Each pair's disagreements follow by tool, with the router's extra cost "
    "over the other there.\n"
)
EVERY_INTRO = (
    "### Where routers disagree, every question\n\n"
    "The same comparison on every question, for the routers that learn nothing from the training split (no "
    "`fit`): `static`, `always:` and a file router without one. For them the held-out split only costs "
    "questions.\n"
)
COMPARE_HEAD = [
    "| Router | Against | Questions | Different path | Different tool | Only router right / only other right "
    "| McNemar p | Accuracy, 95% interval (router / other) | Mean cost, both known (router / other) |",
    "|---|---|---|---|---|---|---|---|---|",
]


def _signed_usd(value: float) -> str:
    return f"{'-' if value < 0 else '+'}${abs(value):.4f}"


def _usd(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.4f}"


def _unknowns(cell: Mapping[str, Any]) -> str:
    keys = (("cost_unknown", "cost"), ("latency_unknown", "time"))
    shown = [f"{cell[k]} {label} unknown" for k, label in keys if cell[k]]
    return f" ({', '.join(shown)})" if shown else ""


def _left_out(cell: Mapping[str, Any], key: str) -> str:
    """`` (n unknown)`` after a sum that left out ``n`` questions, so a partial sum never reads as whole."""
    return f" ({cell[key]} unknown)" if cell[key] else ""


def cell_text(cell: Mapping[str, Any] | None) -> str:
    """One matrix cell: questions (right), overspend, time over, and any extra left unknown."""
    if not cell:
        return NO_CELL
    return (
        f"{cell['questions']} ({cell['correct']} right), ${cell['overspend_usd']:.4f}, "
        f"+{cell['overtime_s']:.1f}s{_unknowns(cell)}"
    )


def _totals(confusion: Mapping[str, Mapping[str, Any]]) -> list[str]:
    out = [
        "| Router | Questions | Wrong tool, wrong answer | Right, not the cheapest tool | Cost regret of those "
        "| Overspend, every question | Per question | Accuracy lost to the oracle | Time over, per question |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for spec, m in confusion.items():
        wrong, dear, every = m["wrong_tool"], m["not_cheapest"], m["all"]
        per_q, per_s, lost = m["overspend_per_question_usd"], m["overtime_per_question_s"], m["accuracy_lost"]
        lost_text = NO_CELL if lost is None else f"{m['oracle_correct'] - every['correct']} ({_p(lost)})"
        time_text = NO_CELL if per_s is None else f"+{per_s:.1f}s{_left_out(every, 'latency_unknown')}"
        out.append(
            f"| `{spec}` | {every['questions']} | {wrong['questions']} | {dear['questions']} "
            f"| ${dear['overspend_usd']:.4f}{_left_out(dear, 'cost_unknown')} "
            f"| ${every['overspend_usd']:.4f}{_left_out(every, 'cost_unknown')} "
            f"| {NO_CELL if per_q is None else _usd(per_q) + _left_out(every, 'cost_unknown')} "
            f"| {lost_text} | {time_text} |"
        )
    return [*out, ""]


def _matrix(spec: str, m: Mapping[str, Any]) -> list[str]:
    best = m["best_tools"]
    out = [
        f"#### `{spec}`\n",
        "| Ran \\ best | " + " | ".join(best) + " |",
        "|---|" + "---|" * len(best),
    ]
    for chosen in m["chosen_tools"]:
        row = m["cells"].get(chosen, {})
        out.append(f"| {chosen} | " + " | ".join(cell_text(row.get(b)) for b in best) + " |")
    return [*out, ""]


def matrix_section(s: Mapping[str, Any]) -> list[str]:
    """Every router's held-out confusion matrix, the totals first."""
    confusion = s.get("confusion")
    if not confusion:
        return []
    out = [MATRIX_INTRO, *_totals(confusion)]
    for spec, m in confusion.items():
        out += _matrix(spec, m)
    return out


def _pattern(c: Mapping[str, Any]) -> list[str]:
    out = [
        f"#### `{c['router']}` against `{c['against']}`\n",
        "| Router's tool | Other's tool | Questions | Only router right | Only other right "
        "| Extra cost (router minus other) |",
        "|---|---|---|---|---|---|",
    ]
    for p in c["patterns"]:
        out.append(
            f"| {p['first_tool']} | {p['second_tool']} | {p['questions']} | {p['only_first_right']} "
            f"| {p['only_second_right']} | {_signed_usd(p['extra_cost_usd'])}"
            + (f" ({p['cost_unknown']} unknown)" if p["cost_unknown"] else "")
            + " |"
        )
    return [*out, ""]


def _accuracy(side: Mapping[str, Any]) -> str:
    ci = side["accuracy_ci95"]
    return _p(side["accuracy"]) + (f" ({_p(ci[0])}-{_p(ci[1])})" if ci else "")


def p_value(p: float) -> str:
    """A p-value to two places, or in scientific notation below 0.01, so a strong result never reads as 0."""
    return f"{p:.2f}" if p >= 0.01 else f"{p:.1e}"


def _compare_row(c: Mapping[str, Any]) -> str:
    a, b = c["first"], c["second"]
    unknown = f" ({c['cost_unknown']} unknown)" if c["cost_unknown"] else ""
    return (
        f"| `{c['router']}` | `{c['against']}` | {c['questions']} | {c['disagree']} | {c['tool_disagree']} "
        f"| {c['only_first_right']} / {c['only_second_right']} | {p_value(c['mcnemar_p'])} "
        f"| {_accuracy(a)} / {_accuracy(b)} | {_usd(a['mean_cost_usd'])} / {_usd(b['mean_cost_usd'])}{unknown} |"
    )


def compare_section(s: Mapping[str, Any]) -> list[str]:
    """Each pair of routers compared: one summary row each, then each pair's disagreements by tool."""
    pairs = s.get("comparisons")
    if not pairs:
        return []
    out = [COMPARE_INTRO, *COMPARE_HEAD, *(_compare_row(c) for c in pairs), ""]
    for c in pairs:
        if c["patterns"]:
            out += _pattern(c)
    every = s.get("comparisons_every_question")
    if every:
        out += [EVERY_INTRO, *COMPARE_HEAD, *(_compare_row(c) for c in every), ""]
    return out

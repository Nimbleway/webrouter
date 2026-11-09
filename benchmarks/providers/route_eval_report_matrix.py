"""route-eval's report on tool choice: each router's cost-weighted confusion matrix, and where routers disagree.

The numbers are :mod:`route_eval_matrix`'s; :mod:`route_eval_report` appends
these sections to each set's. Imported as a sibling, by name, as ``_common`` is.
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
    "A cell reads *questions (right), extra cost, extra time*: the router's cost and latency minus the oracle's "
    "on those questions, as the replay charged them, signed, so a cheaper wrong choice is negative and pays in "
    "accuracy instead. An extra with either side unknown is left out of the sum and counted unknown. A "
    "**misroute** is a question some path got right, served by another tool than the best; its cost is the "
    "extra of those questions, signed, its overspend only the dearer ones', and per question divides the cost "
    "by every held-out question.\n"
)
COMPARE_INTRO = (
    "### Where routers disagree, held out\n\n"
    "Each router against `static` and every `--against` router, on the same held-out questions: how often the "
    "path that served differs (another backend of the same tool counts), how often the tool does, and on "
    "those questions which of the two alone was right. Accuracy is correct out of the held-out questions and "
    "mean cost is over the questions whose cost is known, as in the table above. Each pair's disagreements "
    "follow by tool, with the router's extra cost over the other there.\n"
)


def _signed_usd(value: float) -> str:
    return f"{'-' if value < 0 else '+'}${abs(value):.4f}"


def _signed_s(value: float) -> str:
    return f"{value:+.1f}s"


def _usd(value: float | None, unknown: int = 0) -> str:
    text = "unknown" if value is None else f"${value:.4f}"
    return text + (f" ({unknown} unknown)" if unknown else "")


def _unknowns(cell: Mapping[str, Any]) -> str:
    keys = (("cost_unknown", "cost"), ("latency_unknown", "time"))
    shown = [f"{cell[k]} {label} unknown" for k, label in keys if cell[k]]
    return f" ({', '.join(shown)})" if shown else ""


def _left_out(cell: Mapping[str, Any], key: str) -> str:
    """`` (n unknown)`` after a sum that left out ``n`` questions, so a partial sum never reads as whole."""
    return f" ({cell[key]} unknown)" if cell[key] else ""


def cell_text(cell: Mapping[str, Any] | None) -> str:
    """One matrix cell: questions (right), extra cost, extra time, and any extra left unknown."""
    if not cell:
        return NO_CELL
    return (
        f"{cell['questions']} ({cell['correct']} right), {_signed_usd(cell['extra_cost_usd'])}, "
        f"{_signed_s(cell['extra_latency_s'])}{_unknowns(cell)}"
    )


def _totals(confusion: Mapping[str, Mapping[str, Any]]) -> list[str]:
    out = [
        "| Router | Misrouted | Right of those | Extra cost of misroutes | Overspend on misroutes | Per question "
        "| Extra time of misroutes | Per question | Extra cost over the oracle, every question |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for spec, m in confusion.items():
        mis, every = m["misrouted"], m["all"]
        per_q = m["misroute_cost_per_question_usd"]
        per_s = m["misroute_latency_per_question_s"]
        out.append(
            f"| `{spec}` | {mis['questions']} of {every['questions']} | {mis['correct']} "
            f"| {_signed_usd(mis['extra_cost_usd'])}{_unknowns(mis)} | ${mis['overspend_usd']:.4f} "
            f"| {NO_CELL if per_q is None else _signed_usd(per_q) + _left_out(mis, 'cost_unknown')} "
            f"| {_signed_s(mis['extra_latency_s'])}{_left_out(mis, 'latency_unknown')} "
            f"| {NO_CELL if per_s is None else _signed_s(per_s) + _left_out(mis, 'latency_unknown')} "
            f"| {_signed_usd(every['extra_cost_usd'])}{_left_out(every, 'cost_unknown')} |"
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
    """Every router's held-out confusion matrix, the misroutes' totals first."""
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


def compare_section(s: Mapping[str, Any]) -> list[str]:
    """Each pair of routers compared: one summary row each, then each pair's disagreements by tool."""
    pairs = s.get("comparisons")
    if not pairs:
        return []
    out = [
        COMPARE_INTRO,
        "| Router | Against | Questions | Different path | Different tool | Only router right | Only other right "
        "| Accuracy (router / other) | Mean cost (router / other) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in pairs:
        a, b = c["first"], c["second"]
        out.append(
            f"| `{c['router']}` | `{c['against']}` | {c['questions']} | {c['disagree']} | {c['tool_disagree']} "
            f"| {c['only_first_right']} | {c['only_second_right']} | {_p(a['accuracy'])} / {_p(b['accuracy'])} "
            f"| {_usd(a['mean_cost_usd'], a['cost_unknown'])} / {_usd(b['mean_cost_usd'], b['cost_unknown'])} |"
        )
    out.append("")
    for c in pairs:
        if c["patterns"]:
            out += _pattern(c)
    return out

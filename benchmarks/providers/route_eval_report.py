"""route-eval's report: the markdown ``route_eval.py`` prints, one section per set.

Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from _common import _p, _s
from route_eval_floor import Floor
from route_eval_fmt import _unknown, _usd
from route_eval_paths import EFFORT_INTRO, MIXED, MIXED_INTRO
from route_eval_rank import RANKING_RULE, TOO_FEW, TOO_FEW_RULE
from route_eval_report_matrix import compare_section, matrix_section
from route_eval_sets import TWIN_SETS


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 1000:.2f} ms"


def _pts(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}"


def _intro(floor: float, pairs: Mapping[str, tuple[float, float]]) -> list[str]:
    spread = ", ".join(f"`{p}` {a:.1%} vs {b:.1%}" for p, (a, b) in pairs.items())
    return [
        "# route-eval: routers replayed over the frozen results\n",
        f"**Noise floor: {floor * 100:.1f} points**, the largest accuracy gap one backend shows between "
        f"`{TWIN_SETS[0]}` and `{TWIN_SETS[1]}`, the same search questions run twice ({spread}). Each set is "
        "ranked and judged with its method's own rerun spread where one is frozen, else with this one, and names "
        "the floor it used. It is rerun noise, not a significance test on the "
        "held-out split, so a verdict needs both: *gain* when the held-out margin over the static order is wider "
        "than the floor and the static order's accuracy is below the router's 95% interval, *loss* when the margin "
        "is under minus the floor and the static order is above it, *no gain* otherwise.\n",
        "Accuracy is correct out of every question, a question no backend served counting as a miss. Regret is "
        "in accuracy points (positive: behind). Cost is the backend's stated cost, else its list price from "
        "`web_router.prices` (an estimate); the mean is over the questions whose cost is known, and the count "
        "unknown is shown. Latency adds every failed attempt before the one that served, and the router's own time; "
        "a question with any attempt's latency unrecorded is left out of the mean and counted unknown.\n",
    ]


def _router_table(s: Mapping[str, Any]) -> list[str]:
    out = [
        "| Router | Accuracy in-sample | Accuracy held out | 95% CI held out | Regret vs oracle (in / out) "
        "| Regret vs static (in / out) | Mean cost held out | Mean latency held out | Fallbacks held out "
        "| Router failed held out | Ungraded held out | Router overhead | Held out vs static |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for spec, r in s["routers"].items():
        i, h = r["in_sample"], r["held_out"]
        ci = h["accuracy_ci95"]
        out.append(
            f"| `{spec}` | {_p(i['accuracy'])} | {_p(h['accuracy'])} ({h['correct']}/{h['questions']}) "
            f"| {_p(ci[0]) + '-' + _p(ci[1]) if ci else 'n/a'} "
            f"| {_pts(i['regret_oracle'])} / {_pts(h['regret_oracle'])} "
            f"| {_pts(i['regret_static'])} / {_pts(h['regret_static'])} "
            f"| {_usd(h['mean_cost_usd'], h['cost_unknown'])} "
            f"| {_unknown(_s(h['mean_latency_s']), h['latency_unknown'])} "
            f"| {h['fallbacks']} | {h['router_failed']} | {h['ungraded']} "
            f"| {_ms(h['mean_overhead_s'])} | {r['verdict']} |"
        )
    return [*out, ""]


def _order_table(s: Mapping[str, Any]) -> list[str]:
    out = [
        f"### Recommended order, from every question\n\n{RANKING_RULE} {TOO_FEW_RULE}\n",
        f"| Order | {'Path' if s['method'] == MIXED else 'Backend'} | Correct of the questions counted "
        "| Cost per call | p50 latency | Failed | Left out (rate limit or quota) |",
        "|---|---|---|---|---|---|---|",
    ]
    places = [(str(rank), p) for rank, p in enumerate(s["recommended_order"], 1)]
    for place, p in places + [(TOO_FEW, p) for p in s["too_few_questions"]]:
        b = s["backends"][p]
        out.append(
            f"| {place} | `{p}` | {_p(b['accuracy_all'])} ({b['correct']}/{b['questions']}) "
            f"| {_usd(b['cost_usd'])} | {_s(b['latency_p50'])} | {b['failed']} | {b['excluded']} |"
        )
    return [*out, ""]


def _effort_table(s: Mapping[str, Any]) -> list[str]:
    out = [
        EFFORT_INTRO,
        "| Backend | Effort | Correct of the questions counted | Mean cost | p50 latency | Failed | Left out |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in s["efforts"]:
        out.append(
            f"| `{e['backend']}` | {e['effort']} | {_p(e['accuracy_all'])} ({e['correct']}/{e['questions']}) "
            f"| {_usd(e['cost_usd'])} | {_s(e['latency_p50'])} | {e['failed']} | {e['excluded']} |"
        )
    return [*out, ""]


def _set_section(s: Mapping[str, Any]) -> list[str]:
    out = [
        f"## `{s['set']}` ({s['method']}): {s['questions']} questions, {s['train']} in-sample and "
        f"{s['held_out']} held out (seed {s['seed']})\n"
    ]
    if s["method"] == MIXED:
        out.append(MIXED_INTRO.format(held_out=s["held_out"], step=100 / max(s["held_out"], 1)))
    if not s["prompts"]:
        out.append(
            "Prompt: unavailable -- routers were given `None` for the question text. Run `frozen.py join` and "
            "pass `--joined` for routers that read it; the baselines below need none.\n"
        )
    out.append(f"Noise floor here: {Floor.from_json(s['method'], s['noise_floor']).describe()}.\n")
    out.append("Static order: " + ", ".join(f"`{p}`" for p in s["static_order"]) + "\n")
    efforts = _effort_table(s) if s.get("efforts") else []
    return out + _router_table(s) + matrix_section(s) + compare_section(s) + _order_table(s) + efforts


def markdown(summaries: Sequence[dict[str, Any]], floor: float, pairs: Mapping[str, tuple[float, float]]) -> str:
    out = _intro(floor, pairs)
    for s in summaries:
        out += _set_section(s)
    return "\n".join(out)

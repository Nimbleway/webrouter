"""route-eval's report: the markdown ``route_eval.py`` prints, one section per set.

Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from _common import _p, _s
from route_eval_sets import TWIN_SETS


def _usd(value: float | None, unknown: int = 0) -> str:
    if value is None:
        return f"unknown ({unknown})" if unknown else "unknown"
    return _unknown(f"${value:.4f}", unknown)


def _unknown(text: str, unknown: int) -> str:
    return text + (f" ({unknown} unknown)" if unknown else "")


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 1000:.2f} ms"


def _pts(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}"


def _intro(floor: float, pairs: Mapping[str, tuple[float, float]]) -> list[str]:
    spread = ", ".join(f"`{p}` {a:.1%} vs {b:.1%}" for p, (a, b) in pairs.items())
    return [
        "# route-eval: routers replayed over the frozen results\n",
        f"**Noise floor: {floor * 100:.1f} points**, the largest accuracy gap one backend shows between "
        f"`{TWIN_SETS[0]}` and `{TWIN_SETS[1]}`, the same questions run twice ({spread}). A margin no larger is "
        "*no gain*. It is measured on SealQA and used for every set, the only twin run there is.\n",
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
        "### Recommended order, from every question\n\n"
        "By correct out of every question in the set; within the noise floor of the best not yet placed, the "
        "cheaper backend first (an unknown or $0 cost never moves one up), then the faster, then the name. "
        "Keyless backends go last. A backend that served nothing is left out.\n",
        "| Order | Backend | Correct of all questions | Cost per call | p50 latency | Failed |",
        "|---|---|---|---|---|---|",
    ]
    for rank, p in enumerate(s["recommended_order"], 1):
        b = s["backends"][p]
        out.append(
            f"| {rank} | `{p}` | {_p(b['accuracy_all'])} ({b['correct']}/{b['questions']}) "
            f"| {_usd(b['cost_usd'])} | {_s(b['latency_p50'])} | {b['failed']} |"
        )
    return [*out, ""]


def _set_section(s: Mapping[str, Any]) -> list[str]:
    out = [
        f"## `{s['set']}` ({s['method']}): {s['questions']} questions, {s['train']} in-sample and "
        f"{s['held_out']} held out (seed {s['seed']})\n"
    ]
    if not s["prompts"]:
        out.append(
            "Prompt: unavailable -- routers were given `None` for the question text. Run `frozen.py join` and "
            "pass `--joined` for routers that read it; the baselines below need none.\n"
        )
    out.append("Static order: " + ", ".join(f"`{p}`" for p in s["static_order"]) + "\n")
    return out + _router_table(s) + _order_table(s)


def markdown(summaries: Sequence[dict[str, Any]], floor: float, pairs: Mapping[str, tuple[float, float]]) -> str:
    out = _intro(floor, pairs)
    for s in summaries:
        out += _set_section(s)
    return "\n".join(out)

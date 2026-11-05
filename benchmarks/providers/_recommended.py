"""The provider scan's recommended ``auto`` order: route-eval's ranking over a scan, and the report section for it.

Apart from ``run.py`` so the scan's report and route-eval share one ranking
without the scan growing. Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import json
from typing import Any

from _common import APART, DEPTH_MARK, MODE_MARK, _p, _wilson
from _questions import QUESTION_SETS
from route_eval_rank import noise_floor, ranked_with_best

from web_router import prices
from web_router.constants import PROVIDER_AUTO

ORDER_LABEL = {**APART, None: "best, within the noise floor"}
"""The recommended order's last column: ``None`` is every backend the noise floor does not tell from the best."""


def recommended_order(summary: dict[str, Any], spread: float | None = None) -> list[tuple[str, bool | None]]:
    """The plain backends in route-eval's order, each with whether this run shows it behind the most accurate.

    Ranked by ``route_eval_rank.rank_backends``, the one ranking the repo prints:
    correct over all questions asked, a failed search counting as a miss, not
    accuracy over the searches served -- a chain member that fails most
    searches is no member to put first, however well it answered the few it
    served. Within route-eval's noise floor the cheaper backend at list price
    goes first, then the faster; the keyless tier goes last. A
    ``provider@depth`` or ``provider:mode`` setup is left out: an ``auto``
    member is a backend, sent whatever depth the request asks for, not a mode.
    So is ``auto`` itself, if a scan named it: as a member it would be a second
    ``auto`` on the default order, reaching unranked backends first. So is a
    backend that served nothing; one that served searches and got every answer
    wrong is ranked, last. ``None`` marks every backend within the noise floor
    of the most accurate one -- the best, as far as the floor tells -- wherever
    the cost tie-break placed it; of the rest, a backend is behind only when its
    95% interval stops short of the most accurate one's.
    """
    overall = summary["overall"]
    plain = [
        p
        for p in summary["setup"]["providers"]
        if DEPTH_MARK not in p and MODE_MARK not in p and p != PROVIDER_AUTO and overall[p]["served"]
    ]
    if not plain:
        return []
    stats = {
        p: {
            "accuracy": _answered(overall[p]),
            "cost_usd": prices.estimate(p, "search"),
            "latency_p50": overall[p]["latency_p50"],
        }
        for p in plain
    }
    ranked, best = ranked_with_best(stats, noise_floor()[0] if spread is None else spread)
    floor = _answered_ci(overall[max(plain, key=lambda p: _answered(overall[p]))])[0]
    # Labelled by accuracy, not position: the cost tie-break may put a less accurate backend first.
    return [(p, None if p in best else _answered_ci(overall[p])[1] < floor) for p in ranked]


def _answered(score: dict[str, Any]) -> float:
    """Correct answers out of every question asked, a failed search counting as a miss."""
    return score["correct"] / score["questions"] if score["questions"] else 0.0


def _answered_ci(score: dict[str, Any]) -> tuple[float, float]:
    return _wilson(score["correct"], score["questions"]) or (0.0, 0.0)


def recommendation(summary: dict[str, Any]) -> list[str]:
    """The report's recommended-order section: the order, how each stands against the best, and a chain to paste."""
    spread = noise_floor()[0]
    order = recommended_order(summary, spread)
    lines = ["## Recommended auto order\n"]
    if not order:
        lines.append("No backend in this scan served a search, so there is no order to suggest.\n")
        return lines
    overall = summary["overall"]
    short = QUESTION_SETS[summary["setup"].get("questions", "simpleqa")]["short"]
    lines.append(
        f"A suggestion from one judge (`{summary['setup']['judge']}`) on {short}, not the library's default: the "
        "plain backends in this scan by correct answers out of every question asked, a failed search counting as a "
        "miss -- so a backend that fails often ranks by what it got right, not by its accuracy on the few it served. "
        "A `provider@depth` or `provider:mode` setup is left out, "
        "because a chain member is a backend -- `auto` sends it whatever depth the request asks for -- not a mode. "
        f"Backends within the noise floor ({spread:.1%}, the spread between two runs of the same SealQA "
        "questions; see `route_eval.py`) are ordered cheapest first at list price (`web_router.prices`, an "
        "estimate; an unknown or $0 price never moves one up), then fastest; the keyless tier goes last. "
        "Every backend within the noise floor of the most accurate one is *best, within the noise floor*, wherever "
        "the cost tie-break put it. Of the rest, a backend is *behind* only when its 95% interval stops short of "
        "the most accurate one's; the others are "
        "not shown apart by this run, and another could order them differently.\n"
    )
    lines.append("| Order | Backend | Correct of all questions | 95% CI | Failed | Against the best |")
    lines.append("|---|---|---|---|---|---|")
    for rank, (p, behind) in enumerate(order, 1):
        s = overall[p]
        ci = _answered_ci(s)
        lines.append(
            f"| {rank} | `{p}` | {_p(_answered(s))} ({s['correct']}/{s['questions']}) | {_p(ci[0])}-{_p(ci[1])} "
            f"| {s['failed']} | {ORDER_LABEL[behind]} |"
        )
    chain = json.dumps({"auto": {"chain": {"search": [p for p, _ in order]}}})
    lines.append(
        "\nIt is opt-in: to try it, give your own router the order. The chain replaces the default one, keyless "
        "tail included, and each member still needs its key; keyed by `search`, it leaves the other methods' "
        "chains alone.\n"
    )
    lines.append(f"```python\nWebRouter(provider_config={chain})\n```\n")
    lines.append("The library's default order changes only by a pull request backed by a scan like this one.\n")
    return lines

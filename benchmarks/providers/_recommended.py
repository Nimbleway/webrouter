"""The provider scan's recommended ``auto`` order: route-eval's ranking over a scan, and the report section for it.

Apart from ``run.py`` so the scan's report and route-eval share one ranking
without the scan growing. Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from _common import APART, DEPTH_MARK, MODE_MARK, _p, _wilson
from _questions import QUESTION_SETS
from frozen import frozen_row
from route_eval_rank import (
    MIN_QUESTIONS,
    RANKING_RULE,
    TOO_FEW,
    TOO_FEW_RULE,
    backend_stats,
    noise_floor,
    rankable,
    ranked_with_best,
    too_few,
)
from route_eval_sets import Question

from web_router.constants import AUTO_CHAIN_SEARCH, PROVIDER_AUTO

ORDER_LABEL = {**APART, None: "best, within the noise floor"}
"""The recommended order's last column: ``None`` is every backend the noise floor does not tell from the best."""


def ranking_stats(rows: list[dict[str, Any]], setup: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """route-eval's numbers for each plain backend over a scan's rows: ``route_eval_rank.backend_stats``.

    Each row is first classed as the frozen sets class it (``frozen.frozen_row``),
    so the scan and route-eval rank the same rows alike: a search that failed
    on a rate-limit or quota error is left out, counted in ``excluded``, and the
    latency median is over every served row, one the judge did not grade
    included. A ``provider@depth`` or ``provider:mode`` setup is left out: an
    ``auto`` member is a backend, sent whatever depth the request asks for, not
    a mode. So is ``auto`` itself, if a scan named it: as a member it would be a
    second ``auto`` on the default order, reaching unranked backends first.
    """
    plain = {p for p in setup["providers"] if DEPTH_MARK not in p and MODE_MARK not in p and p != PROVIDER_AUTO}
    by_question: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row["provider"] in plain:
            frozen = frozen_row(str(setup.get("questions")), AUTO_CHAIN_SEARCH, row)
            by_question.setdefault(frozen["question_id"], {})[frozen["provider"]] = frozen
    return backend_stats([Question(q, None, members) for q, members in by_question.items()])


def recommended_order(summary: dict[str, Any], spread: float | None = None) -> list[tuple[str, bool | None]]:
    """The plain backends in route-eval's order, each with whether this run shows it behind the most accurate.

    Ranked by ``route_eval_rank.rank_backends``, the one ranking the repo prints
    (:data:`~route_eval_rank.RANKING_RULE`), over the summary's ``ranking``
    (:func:`ranking_stats`), at each row's stated cost, else its list price from
    ``web_router.prices`` (search rows state none, so in practice the list price). A
    backend that served nothing is left out, and so is one counted on fewer than
    :data:`~route_eval_rank.MIN_QUESTIONS` (:func:`route_eval_rank.too_few`), as
    route-eval's ``recommended`` leaves it out; one that served searches and got
    every answer wrong is ranked, last. ``None`` marks every backend within the
    noise floor of the most accurate one -- the best, as far as the floor tells
    -- wherever the cost tie-break placed it; of the rest, a backend is behind
    only when its 95% interval stops short of the most accurate one's.
    """
    ranking = summary["ranking"]
    stats = rankable(ranking)
    if not stats:
        return []
    ranked, best = ranked_with_best(stats, noise_floor()[0] if spread is None else spread)
    floor = _answered_ci(ranking[max(stats, key=lambda p: stats[p]["accuracy"])])[0]
    # Labelled by accuracy, not position: the cost tie-break may put a less accurate backend first.
    return [(p, None if p in best else _answered_ci(ranking[p])[1] < floor) for p in ranked]


def _answered_ci(score: dict[str, Any]) -> tuple[float, float]:
    return _wilson(score["correct"], score["questions"]) or (0.0, 0.0)


def _order_table(
    order: list[tuple[str, bool | None]], few: list[str], ranking: Mapping[str, dict[str, Any]]
) -> list[str]:
    """The recommended order's table: the ranked backends, then those counted on too few questions."""
    lines = [
        "| Order | Backend | Correct of all questions | 95% CI | Failed | Rate limit or quota | Against the best |",
        "|---|---|---|---|---|---|---|",
    ]
    places = [(str(rank), p, ORDER_LABEL[behind]) for rank, (p, behind) in enumerate(order, 1)]
    for place, p, against in places + [(TOO_FEW, p, TOO_FEW) for p in few]:
        s = ranking[p]
        ci = _answered_ci(s)
        lines.append(
            f"| {place} | `{p}` | {_p(s['accuracy_all'])} ({s['correct']}/{s['questions']}) | {_p(ci[0])}-{_p(ci[1])} "
            f"| {s['failed']} | {s['excluded']} | {against} |"
        )
    return lines


def recommendation(summary: dict[str, Any]) -> list[str]:
    """The report's recommended-order section: the order, how each stands against the best, and a chain to paste."""
    spread = noise_floor()[0]
    order = recommended_order(summary, spread)
    ranking = summary["ranking"]
    few = too_few(ranking)
    lines = ["## Recommended auto order\n"]
    if not order and not few:
        lines.append("No backend in this scan served a search, so there is no order to suggest.\n")
        return lines
    short = QUESTION_SETS[summary["setup"].get("questions", "simpleqa")]["short"]
    lines.append(
        f"A suggestion from one judge (`{summary['setup']['judge']}`) on {short}, not the library's default: the "
        "plain backends in this scan in route-eval's order. A `provider@depth` or `provider:mode` setup is left out, "
        "because a chain member is a backend -- `auto` sends it whatever depth the request asks for -- not a mode. "
        f"The noise floor is {spread:.1%}, the spread between two runs of the same SealQA questions (see "
        "`route_eval.py`), and cost is a row's stated cost, else its list price in `web_router.prices`, an "
        "estimate (search backends state none). "
        "A search that failed on a rate-limit or quota error is counted under *Rate limit or quota*, as route-eval "
        "counts it, and nowhere else. "
        f"{RANKING_RULE} {TOO_FEW_RULE} "
        "Every backend within the noise floor of the most accurate one is *best, within the noise floor*, wherever "
        "the cost tie-break put it. Of the rest, a backend is *behind* only when its 95% interval stops short of "
        "the most accurate one's; the others are "
        "not shown apart by this run, and another could order them differently.\n"
    )
    lines.extend(_order_table(order, few, ranking))
    if not order:
        lines.append(
            f"\nNo backend in this scan was counted on at least {MIN_QUESTIONS} questions, "
            "so there is no order to suggest.\n"
        )
        return lines
    chain = json.dumps({"auto": {"chain": {"search": [p for p, _ in order]}}})
    lines.append(
        "\nIt is opt-in: to try it, give your own router the order. The chain replaces the default one, keyless "
        "tail included, and each member still needs its key; keyed by `search`, it leaves the other methods' "
        "chains alone.\n"
    )
    lines.append(f"```python\nWebRouter(provider_config={chain})\n```\n")
    lines.append("The library's default order changes only by a pull request backed by a scan like this one.\n")
    return lines

"""route-eval's ``fitted`` router: the recommended order, learned on the training split, for every question.

It ranks every candidate it was fit on by :data:`~route_eval_rank.RANKING_RULE`
-- accuracy in noise-floor tiers, cheapest first within one -- and answers that
order on every held-out question. On a set loaded with ``--modes`` a vendor
mode is a candidate like a backend, so the same router, run with and without
``--modes``, shows what routing on modes adds over routing on backends. Its
answer is a decision's ``attempts`` (``search:exa:exa-deep``), merged as a
routed call merges one. Imported as a sibling, by name, as ``_common`` is.

Experimental until 1.0, like the rest of route-eval.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from route_eval_floor import floor_for
from route_eval_paths import MIXED
from route_eval_rank import backend_stats, recommended
from route_eval_sets import Question

from web_router.routing.contract import RouteDecision, candidate_id
from web_router.routing.entry_names import split_entry

FITTED = "fitted"


def fitted_router(method: str) -> Callable[[Question], Any]:
    """The order :func:`~route_eval_rank.recommended` gives the training questions, ranked with ``method``'s floor.

    On a mixed set the order is of paths and is answered as a ranking; a candidate it was not fit on falls to the
    static order, as an unranked one does in a routed call.
    """
    floor = floor_for(method).value
    order: list[str] = []

    def fit(train: Sequence[Question]) -> None:
        order[:] = recommended(backend_stats(train), floor)

    def route(q: Question) -> Any:
        ranked = [p for p in order if p in q.rows]
        if method == MIXED:
            return ranked
        return RouteDecision(attempts=tuple(candidate_id(method, *split_entry(p, ())) for p in ranked))

    route.fit = fit  # type: ignore[attr-defined]
    return route

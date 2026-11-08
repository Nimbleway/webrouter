"""route-eval's replay: one question through a router, walked as ``auto`` walks its chain, and what it came to.

Apart from :mod:`route_eval` so each stays small; :mod:`route_eval` re-exports
:class:`Outcome` and :func:`replay`. Experimental until 1.0.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from route_eval_merge import walk
from route_eval_rank import cost_of, is_served
from route_eval_routers import Router
from route_eval_sets import Question

from web_router.constants_merge import ROUTER_ERROR
from web_router.routing.merge import FallbackReason

MSG_BAD_COST = "the router stated a cost of {cost}; a cost is a finite number of dollars, 0 or more"


@dataclass(frozen=True)
class Outcome:
    """What a router's choice came to on one question."""

    correct: bool
    served_by: str | None
    latency_s: float | None
    """``None`` when any attempt's latency is unknown: the total is then unknown too."""
    cost_usd: float | None
    fallback: bool
    """The router's first choice did not serve: it failed, returned nothing usable, or its backend's row failed."""
    router_failed: bool
    overhead_s: float
    router_error: str | None = None
    """The exception the router raised, as ``Type: message``; ``None`` when it raised nothing."""
    ungraded: bool = False
    """The backend served, but the judge failed or the answer leaked the set's own pages: never correct."""
    unscorable: int = 0
    """Backends tried, in order, with no frozen row to score, such as research at an effort never run: misses."""


def _decision(decision: Any) -> tuple[Any, float]:
    """A router's answer -- a ``RouteDecision``, a ranking, or either with its stated cost -- and that cost."""
    if isinstance(decision, Mapping):
        cost = float(decision.get("cost_usd") or 0.0)
        if not math.isfinite(cost) or cost < 0:
            raise ValueError(MSG_BAD_COST.format(cost=cost))
        if "decision" in decision:
            return decision["decision"], cost
        return [str(p) for p in decision.get("ranked") or ()], cost
    if isinstance(decision, str):
        return [decision], 0.0
    if isinstance(decision, list | tuple):
        return [str(p) for p in decision], 0.0
    return decision, 0.0


def replay(router: Router, q: Question, fallback_order: Router) -> Outcome:
    """One question through a router, walked as ``auto`` walks its chain.

    The router's answer goes through the library's merge rule
    (:func:`route_eval_merge.walk`): its ranking first, an id that is not a
    candidate dropped, then every other candidate in the static order. A row
    whose backend answered but went ungraded (the judge failed, or the answer
    leaked the set's own pages) serves the question: no fallback, not correct,
    counted as ungraded. A router that raises, answers with no well-formed
    decision, or ranks no candidate at all counts as failed, and the static
    order is walked. A failed row is passed over -- its time to failure and its
    estimated cost charged, as a routed call's budget charges a failed attempt
    -- and the next one tried; the first choice not serving is a fallback. A
    question nobody served is a miss. The router's own time and stated cost
    are charged on every question.
    """
    started = time.perf_counter()
    error: str | None = None
    failure: FallbackReason | None = None
    try:
        answer, cost = _decision(router(q))
    except Exception as exc:  # a router that raises is scored as failed, not fatal to the replay
        answer, cost, failure = None, 0.0, ROUTER_ERROR
        error = f"{type(exc).__name__}: {exc}"
    overhead = time.perf_counter() - started
    ranked, router_failed = walk(answer, q, fallback_order, failure)
    unscorable = 0
    latency: float | None = overhead
    total: float | None = cost
    for i, provider in enumerate(ranked):
        if provider is None:  # a backend the order names with no frozen row: a miss, no time, no cost
            unscorable += 1
            continue
        row = q.rows[provider]
        latency = None if latency is None or row["latency_s"] is None else latency + row["latency_s"]
        price = cost_of(row)
        total = None if total is None or price is None else total + price
        if is_served(row):
            fallback = router_failed or i > 0
            ungraded = row["error"] is not None
            correct = not ungraded and row["grade"] == "correct"
            return Outcome(
                correct, provider, latency, total, fallback, router_failed, overhead, error, ungraded, unscorable
            )
    return Outcome(False, None, latency, total, True, router_failed, overhead, error, unscorable=unscorable)

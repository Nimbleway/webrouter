"""What one attempt is estimated to cost and take: the one pricing rule every routing part reads.

Experimental. The ledger charges by it, the candidate builder prices
candidates by it, the effort mapping fits efforts by it, and the merge rule
checks a router's ``search_depth`` by it, so a candidate is never priced one
way when offered and another when run.

- **Cost** is the price list's estimate for (provider, method, mode). It is
  unknown -- ``None`` -- when the list has no price (no row, ``unknown``, or
  billed by tokens), when the row is only a floor with charges billed on top
  (``estimate_is_floor``), when the request carries ``provider_options``,
  which can change the mode the vendor bills, and when a search asks for more
  results than the row's price assumes (``results_assumed``), where each
  result is charged. An extract is charged per URL.
- **p90** is the price list's measured latency; for research, the effort
  table's measured p90 for the effort first (the backend's default when none
  is set), then the price list's. Unknown otherwise, and with
  ``provider_options``, which can change the mode that runs.
"""

from __future__ import annotations

from web_router import prices
from web_router.constants_budget import (
    COST_TOLERANCE_USD,
    FIT_REASONS,
    OVER_COST,
    OVER_TIME,
    UNKNOWN_P90,
    UNKNOWN_PRICE,
)
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget
from web_router.routing.effort_table import RESEARCH, effort_p90
from web_router.types import ExtractRequest, ResearchRequest, SearchRequest


def mode_cost(provider: str, tool: str, mode: str | None) -> float | None:
    """The list price of one ``tool`` call on ``provider`` at ``mode``; ``None`` when unknown or only a floor."""
    row = prices.price(provider, tool, mode)
    if row is None or row.estimate_is_floor or row.estimate_usd is None:
        return None
    return row.estimate_usd


def over_cost(cost: float, left: float) -> bool:
    """Whether an estimate of ``cost`` dollars does not fit ``left``: the one comparison, with a float's tolerance."""
    return cost > left + COST_TOLERANCE_USD


def misfit(
    cost: float | None, p90: float | None, cost_left: float | None, time_left: float | None, strict: bool
) -> str | None:
    """Why an estimate of ``cost`` dollars and ``p90`` seconds does not fit what is left, as a code; ``None`` fits.

    The one fit rule: ``cost_left`` of ``None`` caps no cost, and under a cap an unknown price never fits;
    ``time_left`` of ``None`` caps no time, and under a cap an unknown p90 fits unless ``strict``.
    """
    if cost_left is not None:
        if cost is None:
            return UNKNOWN_PRICE
        if over_cost(cost, cost_left):
            return OVER_COST
    if time_left is not None:
        if p90 is None:
            return UNKNOWN_P90 if strict else None
        if p90 > time_left:
            return OVER_TIME
    return None


def fit_reason(attempt: Attempt, budget: Budget, what: str) -> str | None:
    """Why ``attempt`` does not fit ``budget``, worded with ``what`` naming it; ``None`` when it fits.

    Priced by the attempt rule -- the whole request, its effort, depth, result count and URLs -- and judged
    by :func:`misfit`: the one check a router's option and a mapped effort are held to.
    """
    cost = None if budget.max_cost_usd is None else attempt_cost(attempt)  # each read only under its cap
    p90 = None if budget.time_limit_s is None else attempt_p90(attempt)
    code = misfit(cost, p90, budget.max_cost_usd, budget.time_limit_s, budget.strict)
    if code is None:
        return None
    cap = budget.time_limit_s if code == OVER_TIME else budget.max_cost_usd  # each wording names its own cap
    return FIT_REASONS[code].format(what=what, cost=cost, cap=cap, p90=p90)


def mode_p90(provider: str, tool: str, mode: str | None) -> float | None:
    """The measured p90 seconds of ``tool`` on ``provider`` at ``mode``, or ``None`` when unmeasured."""
    if tool == RESEARCH:
        return effort_p90(provider, mode)
    prior = prices.estimate_latency(provider, tool, mode)
    return None if prior is None else prior[1]


def attempt_mode(attempt: Attempt) -> str | None:
    """The price list's mode for ``attempt``: a search's depth, a research run's effort, else ``None``."""
    request = attempt.request
    if isinstance(request, SearchRequest):
        return request.search_depth
    if isinstance(request, ResearchRequest):
        return request.effort
    return None


def attempt_cost(attempt: Attempt) -> float | None:
    """The estimated list price of ``attempt`` in US dollars, or ``None`` when it cannot be known before the call."""
    if attempt.request.provider_options or _more_results_than_priced(attempt):
        return None
    cost = mode_cost(attempt.provider, attempt.tool, attempt_mode(attempt))
    if cost is not None and isinstance(attempt.request, ExtractRequest):
        return cost * len(attempt.request.urls)
    return cost


def _more_results_than_priced(attempt: Attempt) -> bool:
    """A search asking for more results than its row's price assumes, where each result is charged: unpriced."""
    request = attempt.request
    if not isinstance(request, SearchRequest) or request.max_results is None:
        return False
    row = prices.price(attempt.provider, attempt.tool, attempt_mode(attempt))
    assumed = None if row is None else row.results_assumed
    return assumed is not None and request.max_results > assumed


def attempt_p90(attempt: Attempt) -> float | None:
    """The measured p90 seconds of ``attempt``, or ``None`` when unmeasured or ``provider_options`` may change it."""
    if attempt.request.provider_options:
        return None
    return mode_p90(attempt.provider, attempt.tool, attempt_mode(attempt))

"""What an attempt is estimated to cost and take, from :mod:`web_router.prices`, before it starts. Experimental."""

from __future__ import annotations

from web_router import prices
from web_router.routing.attempts import Attempt
from web_router.types import ExtractRequest, ResearchRequest, SearchRequest


def attempt_mode(attempt: Attempt) -> str | None:
    """The price list's mode for ``attempt``: a search's depth, a research run's effort, else ``None``."""
    request = attempt.request
    if isinstance(request, SearchRequest):
        return request.search_depth
    if isinstance(request, ResearchRequest):
        return request.effort
    return None


def attempt_cost(attempt: Attempt) -> float | None:
    """The estimated list price of ``attempt`` in US dollars, or ``None`` when it cannot be known before the call.

    Unknown when the price list has no price -- no row, ``unknown``, billed by
    tokens -- when the row is only a floor, with charges billed on top
    (``estimate_is_floor``), and when the request carries ``provider_options``,
    which can change the mode the vendor bills (a deeper tier, a pro mode), or
    when a search asks for more results than its row's price assumes, where
    each result is charged (``results_assumed``). An extract is charged per
    URL, each a page or a request.
    """
    if attempt.request.provider_options or _more_results_than_priced(attempt):
        return None
    row = prices.price(attempt.provider, attempt.tool, attempt_mode(attempt))
    if row is None or row.estimate_is_floor or row.estimate_usd is None:
        return None
    if isinstance(attempt.request, ExtractRequest):
        return row.estimate_usd * len(attempt.request.urls)
    return row.estimate_usd


def _more_results_than_priced(attempt: Attempt) -> bool:
    """A search asking for more results than its row's price assumes, where each result is charged: unpriced."""
    request = attempt.request
    if not isinstance(request, SearchRequest) or request.max_results is None:
        return False
    row = prices.price(attempt.provider, attempt.tool, attempt_mode(attempt))
    assumed = None if row is None else row.results_assumed
    return assumed is not None and request.max_results > assumed


def attempt_p90(attempt: Attempt) -> float | None:
    """The measured p90 seconds of ``attempt``, or ``None`` when unmeasured."""
    measured = prices.estimate_latency(attempt.provider, attempt.tool, attempt_mode(attempt))
    return None if measured is None else measured[1]

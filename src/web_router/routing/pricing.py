"""What one attempt is estimated to cost and take: the one pricing rule every routing part reads.

Experimental. The ledger charges by it, the candidate builder prices
candidates by it, the effort mapping fits efforts by it, and the merge rule
checks a router's ``search_depth`` by it, so a candidate is never priced one
way when offered and another when run.

- **Cost** is the price list's estimate for (provider, method, mode). It is
  unknown -- ``None`` -- when the list has no price (no row, ``unknown``, or
  billed by tokens), when the row is only a floor with charges billed on top
  (``estimate_is_floor``), and when the request carries ``provider_options``,
  which can change the mode the vendor bills. An extract is charged per URL.
- **p90** is the price list's measured latency; for research with none there,
  the effort table's measured p90 for the effort, the backend's default when
  none is set. Unknown otherwise, and with ``provider_options``, which can
  change the mode that runs.
"""

from __future__ import annotations

from web_router import prices
from web_router.routing.attempts import Attempt
from web_router.types import ExtractRequest, ResearchRequest, SearchRequest

RESEARCH = "research"


def research_mode(provider: str, effort: str | None) -> str | None:
    """``effort``, or for ``None`` the effort the price list marks as the backend's default, when it marks one."""
    if effort is not None:
        return effort
    row = prices.price(provider, RESEARCH, None)
    return None if row is None else row.mode


def mode_cost(provider: str, tool: str, mode: str | None) -> float | None:
    """The list price of one ``tool`` call on ``provider`` at ``mode``; ``None`` when unknown or only a floor."""
    row = prices.price(provider, tool, mode)
    if row is None or row.estimate_is_floor or row.estimate_usd is None:
        return None
    return row.estimate_usd


def mode_p90(provider: str, tool: str, mode: str | None) -> float | None:
    """The measured p90 seconds of ``tool`` on ``provider`` at ``mode``, or ``None`` when unmeasured."""
    prior = prices.estimate_latency(provider, tool, mode)
    if prior is not None:
        return prior[1]
    if tool != RESEARCH:
        return None
    from web_router.routing.params import measured_efforts  # params prices by this module

    p90 = measured_efforts(provider).get(research_mode(provider, mode) or "", {}).get("latency_p90_s")
    return None if p90 is None else float(p90)


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
    if attempt.request.provider_options:
        return None
    cost = mode_cost(attempt.provider, attempt.tool, attempt_mode(attempt))
    if cost is not None and isinstance(attempt.request, ExtractRequest):
        return cost * len(attempt.request.urls)
    return cost


def attempt_p90(attempt: Attempt) -> float | None:
    """The measured p90 seconds of ``attempt``, or ``None`` when unmeasured or ``provider_options`` may change it."""
    if attempt.request.provider_options:
        return None
    return mode_p90(attempt.provider, attempt.tool, attempt_mode(attempt))

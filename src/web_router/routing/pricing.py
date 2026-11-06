"""What one attempt is estimated to cost and take: the one pricing rule every routing part reads.

Experimental. The ledger charges by it, the candidate builder prices
candidates by it, the effort mapping fits efforts by it, and the merge rule
checks a router's ``search_depth`` by it, so a candidate is never priced one
way when offered and another when run.

- **Cost** is the price list's estimate for (provider, method, mode). It is
  unknown -- ``None`` -- when the list has no price (no row, ``unknown``, or
  billed by tokens), when the row is only a floor with charges billed on top
  (``estimate_is_floor``), and when the request carries ``provider_options``,
  which can change the mode the vendor bills. An extract is charged per URL,
  and a browser session per minute of its lifetime (``timeout``), rounded up
  to whole minutes as vendors bill: unknown without one, and unknown with any
  option that can turn on an extra billed apart -- a proxy, stealth, CAPTCHA
  solving, a profile -- unless the backend is free.
- **p90** is the price list's measured latency; for research with none there,
  the effort table's measured p90 for the effort, the backend's default when
  none is set. Unknown otherwise, and with ``provider_options``, which can
  change the mode that runs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any

from web_router import prices
from web_router.constants_budget import (
    BROWSER_UNBILLED_OPTIONS,
    COST_TOLERANCE_USD,
    OVER_COST,
    OVER_TIME,
    SECONDS_PER_MINUTE,
    UNKNOWN_P90,
    UNKNOWN_PRICE,
)
from web_router.constants_params import EFFORTS_DATA_DIR, EFFORTS_DATA_FILE
from web_router.routing.attempts import Attempt
from web_router.types import BrowserRequest, ExtractRequest, Provider, ResearchRequest, SearchRequest, provider_name

RESEARCH = "research"


@lru_cache(maxsize=1)
def effort_table() -> dict[str, Any]:
    """The whole generated research effort table."""
    text = (resources.files("web_router") / EFFORTS_DATA_DIR / EFFORTS_DATA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def measured_efforts(provider: str | Provider) -> Mapping[str, Mapping[str, Any]]:
    """``provider``'s measured research efforts, by name, each with its ``accuracy`` and latency; empty if none."""
    entry = effort_table()["research"].get(provider_name(provider), {})
    measured: Mapping[str, Mapping[str, Any]] = entry.get("measured", {})
    return measured


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


def mode_p90(provider: str, tool: str, mode: str | None) -> float | None:
    """The measured p90 seconds of ``tool`` on ``provider`` at ``mode``, or ``None`` when unmeasured."""
    prior = prices.estimate_latency(provider, tool, mode)
    if prior is not None:
        return prior[1]
    if tool != RESEARCH:
        return None
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
    if cost and isinstance(attempt.request, BrowserRequest):
        lifetime = attempt.request.timeout
        if lifetime is None or attempt.request.specified_parameters() - BROWSER_UNBILLED_OPTIONS:
            return None
        return cost * math.ceil(lifetime / SECONDS_PER_MINUTE)
    return cost


def attempt_p90(attempt: Attempt) -> float | None:
    """The measured p90 seconds of ``attempt``, or ``None`` when unmeasured or ``provider_options`` may change it."""
    if attempt.request.provider_options:
        return None
    return mode_p90(attempt.provider, attempt.tool, attempt_mode(attempt))

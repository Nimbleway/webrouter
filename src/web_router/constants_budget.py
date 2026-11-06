"""A call's budget: why a candidate was skipped, and what the call raises when nothing fits.

Experimental: :mod:`web_router.routing.budget` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

SKIP_PRICE_UNKNOWN = "price unknown under a cost cap"
SKIP_OVER_COST = "est. {cost} > {left} left"
SKIP_NO_TIME = "no time left"
SKIP_OVER_TIME = "p90 {p90:.1f}s > {left:.1f}s left"
SKIP_LATENCY_UNKNOWN = "no measured p90 under a strict time cap"
SKIP_ROUTE_REFUSED = "refused by the call's route"
"""Why the budget passed over a candidate: in the decision record's hops and in the error."""

SKIPPED = "{id} skipped: {reason}"
MSG_BUDGET_EXCEEDED = "Nothing fits the budget: {skipped}."

UNKNOWN_PRICE = "unknown_price"
OVER_COST = "over_cost"
UNKNOWN_P90 = "unknown_p90"
OVER_TIME = "over_time"
"""Why an estimate does not fit what is left, as :func:`web_router.routing.pricing.misfit` answers: each
caller words the code its own way."""

COST_TOLERANCE_USD = 1e-9
"""An estimate this close to what is left still fits: sums of list prices are not exact in binary."""

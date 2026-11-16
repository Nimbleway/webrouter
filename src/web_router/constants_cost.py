"""What a response's cost fields read and say: a backend's stated currency, and the estimate's sources.

Kept apart so :mod:`web_router.constants` stops growing; it imports nothing of ours.
"""

from __future__ import annotations

COST_CURRENCY_FIELD = "currency"
"""The key a backend's cost object names its currency under, when it names one."""

COST_CURRENCY_USD = "USD"
"""The one currency a stated cost is read in."""

COST_REPORTED = "reported"
COST_ESTIMATED = "estimated"

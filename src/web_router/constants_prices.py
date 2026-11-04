"""The price list's fixed values: where it ships, how a unit divides, its messages, and how PRICING.md shows it.

Experimental: :mod:`web_router.prices` is new and its shape may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

PRICES_DATA_DIR = "data"
PRICES_DATA_FILE = "prices.json"
"""Under the ``web_router`` package, so it ships in the wheel beside the code."""

UNPRICED_UNITS: frozenset[str] = frozenset({"token_billed", "unknown"})
"""Units with no fixed price, so no estimate before the call."""

PER_ONE: dict[str, float] = {
    "per_request": 1.0,
    "per_1k": 1000.0,
    "per_page": 1.0,
    "per_browser_minute": 1.0,
    "per_run": 1.0,
}
"""What ``price_usd`` is divided by to give the cost of one unit: one request, page, run or browser-minute."""

MSG_PRICE_SHAPE = "price row {key}: price_usd must be {expected} for unit {unit!r}"
MSG_PRICE_SOURCE = "price row {key}: a priced row needs source_url and checked"
MSG_PRICE_DUPLICATE = "price rows repeat (provider, method, mode): {keys}"
MSG_PRICE_DEFAULTS = "price rows for {pair} name more than one default call: {modes}"

# How `make prices` writes a row into PRICING.md.
UNIT_TEXT: dict[str, str] = {
    "per_request": "per request",
    "per_1k": "per 1k",
    "per_page": "per page",
    "per_run": "per run",
}
"""A priced unit as PRICING.md's tables word it; ``per_browser_minute`` is shown as an hourly rate."""

MINUTES_PER_HOUR = 60

EMPTY = "—"
"""A table cell with nothing to say."""

MSG_PRICING_STALE = "PRICING.md's tables are stale: run `make prices`."

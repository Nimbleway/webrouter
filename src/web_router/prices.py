"""Estimated list prices for every (provider, method, mode) web-router calls.

Experimental: this module is new and its shape may change before 1.0.

The figures are each vendor's published pay-as-you-go list price, read from the
vendor's own page (``source_url``) on the date in ``checked`` and shipped in
``web_router/data/prices.json``. They are estimates: volume plans and negotiated
rates differ, and a call can bill more than its row says (content add-ons,
tokens, retries). A vendor that publishes no price is ``unknown``, and one that
bills by tokens is ``token_billed``; neither has a pre-call estimate, and
:func:`estimate` returns ``None`` for both rather than guessing.

    >>> from web_router import prices
    >>> prices.estimate("tavily", "search")          # one request at the default depth
    0.008
    >>> prices.estimate("nimble", "research", "low")  # one run
    0.025

A row is keyed by ``provider``, ``method`` and ``mode``. ``method`` is the
router method's name (``search``, ``extract``, ``run_template``, ``answer``,
``research``, ``open_browser``, ``browse``). ``mode`` is ``None`` for the call
that sets nothing, else web-router's own setting -- the ``search_depth``
(``lite``, ``fast``, ``deep``) or research ``effort`` (``low`` ... ``max``) --
or, prefixed ``:``, a vendor mode sent through ``provider_options`` as named in
``benchmarks/providers/modes.json`` (``:ultra-fast``). Where the call that sets
nothing lands on one of those, that row carries ``default`` and answers
``mode=None``.

A row also carries measured latency where a benchmark has it: the median and
90th-percentile seconds of the calls that succeeded (``latency_p50_s``,
``latency_p90_s``), how many there were (``latency_n``), the benchmark runs they
pooled (``latency_runs``) and the date of the newest one (``latency_measured``).
They come from the frozen results in ``benchmarks/providers/frozen/``, written
by ``make latency``, and cover search and answer only; elsewhere they are
``None``, unknown, never guessed. :func:`estimate_latency` reads them.

    >>> prices.estimate_latency("exa", "answer")      # (p50, p90) seconds
    (1.317, 1.618)
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from web_router.constants_prices import (
    MSG_PRICE_DEFAULTS,
    MSG_PRICE_DUPLICATE,
    MSG_PRICE_LATENCY,
    MSG_PRICE_NEGATIVE,
    MSG_PRICE_SHAPE,
    MSG_PRICE_SOURCE,
    PER_ONE,
    PRICES_DATA_DIR,
    PRICES_DATA_FILE,
    UNPRICED_UNITS,
)
from web_router.types import Provider, provider_name

PriceUnit = Literal["per_request", "per_1k", "per_page", "per_browser_minute", "per_run", "token_billed", "unknown"]
"""What ``price_usd`` is charged per. ``per_1k`` is per 1,000 of whatever the method bills: requests, pages, runs."""

PriceMethod = Literal["search", "extract", "run_template", "answer", "research", "open_browser", "browse"]


class Price(BaseModel):
    """One row of the price list: what one (provider, method, mode) costs, and where that figure comes from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    method: PriceMethod
    mode: str | None = None
    default: bool = False
    """This mode is what a call that sets no mode gets."""
    price_usd: float | None
    unit: PriceUnit
    note: str | None = None
    """What the figure leaves out or adds: content add-ons, tokens, a plan range."""
    source_url: str | None
    checked: date | None
    setup: str | None = None
    """How a benchmark setup reaches this mode (``tavily@fast``); for PRICING.md only."""
    setting: str | None = None
    """What web-router sends the vendor for it; for PRICING.md only."""
    does: str | None = None
    """What the mode returns or does; for PRICING.md only."""
    latency_p50_s: float | None = None
    """Median seconds of one successful call in the benchmark runs below; ``None`` when unmeasured."""
    latency_p90_s: float | None = None
    """90th-percentile seconds, nearest rank."""
    latency_n: int | None = None
    """How many successful calls the two figures are over."""
    latency_runs: tuple[str, ...] | None = None
    """The benchmark runs pooled for them, by name, as ``benchmarks/providers/frozen/manifest.json`` lists them."""
    latency_measured: date | None = None
    """The day the newest of those runs started."""

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.provider, self.method, self.mode)

    @property
    def estimate_usd(self) -> float | None:
        """Estimated cost of one request, page, run or browser-minute, or ``None`` with no fixed price."""
        if self.price_usd is None:
            return None
        return self.price_usd / PER_ONE[self.unit]

    @model_validator(mode="after")
    def _check(self) -> Price:
        unpriced = self.unit in UNPRICED_UNITS
        if unpriced != (self.price_usd is None):
            expected = "null" if unpriced else "a number"
            raise ValueError(MSG_PRICE_SHAPE.format(key=self.key, expected=expected, unit=self.unit))
        if self.price_usd is not None and self.price_usd < 0:
            raise ValueError(MSG_PRICE_NEGATIVE.format(key=self.key, price=self.price_usd))
        billed = self.unit == "token_billed" or bool(self.price_usd)
        if billed and (self.source_url is None or self.checked is None):
            raise ValueError(MSG_PRICE_SOURCE.format(key=self.key))
        self._check_latency()
        return self

    def _check_latency(self) -> None:
        fields = (self.latency_p50_s, self.latency_p90_s, self.latency_n, self.latency_runs, self.latency_measured)
        if all(value is None for value in fields):
            return
        p50, p90, n = self.latency_p50_s, self.latency_p90_s, self.latency_n
        if (
            p50 is None
            or p90 is None
            or n is None
            or not self.latency_runs
            or self.latency_measured is None
            or not 0 <= p50 <= p90
            or n < 1
        ):
            raise ValueError(MSG_PRICE_LATENCY.format(key=self.key))


def _validate(rows: tuple[Price, ...]) -> None:
    repeated = [key for key, count in Counter(row.key for row in rows).items() if count > 1]
    if repeated:
        raise ValueError(MSG_PRICE_DUPLICATE.format(keys=repeated))
    defaults: dict[tuple[str, str], list[str | None]] = {}
    for row in rows:
        if row.default or row.mode is None:
            defaults.setdefault((row.provider, row.method), []).append(row.mode)
    for pair, modes in defaults.items():
        if len(modes) > 1:
            raise ValueError(MSG_PRICE_DEFAULTS.format(pair=pair, modes=modes))


@lru_cache(maxsize=1)
def prices() -> tuple[Price, ...]:
    """Every row of the shipped price list, in file order."""
    text = (resources.files("web_router") / PRICES_DATA_DIR / PRICES_DATA_FILE).read_text(encoding="utf-8")
    rows = tuple(Price.model_validate(row) for row in json.loads(text)["rows"])
    _validate(rows)
    return rows


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str, str | None], Price]:
    index = {row.key: row for row in prices()}
    for row in prices():
        if row.default:
            index[(row.provider, row.method, None)] = row
    return index


def price(provider: str | Provider, method: str, mode: str | None = None) -> Price | None:
    """The row for ``(provider, method, mode)``, or ``None`` when the list has none.

    ``mode=None`` is the call that sets no mode: the row whose ``mode`` is
    ``None``, else the one marked ``default``. A mode with no row of its own
    is ``None`` too -- never another mode's price.
    """
    return _index().get((provider_name(provider), method, mode))


def estimate(provider: str | Provider, method: str, mode: str | None = None) -> float | None:
    """Estimated list price in US dollars of one call: one request, page, run or browser-minute.

    ``None`` when the price is unknown, billed by tokens, or not in the list.
    """
    row = price(provider, method, mode)
    return None if row is None else row.estimate_usd


def estimate_latency(provider: str | Provider, method: str, mode: str | None = None) -> tuple[float, float] | None:
    """Measured ``(p50, p90)`` seconds of one successful call, or ``None`` when unmeasured or not in the list.

    Experimental. The figures are benchmark medians and 90th percentiles, not a
    promise: a backend's latency drifts from run to run and with the query.
    """
    row = price(provider, method, mode)
    if row is None or row.latency_p50_s is None or row.latency_p90_s is None:
        return None
    return row.latency_p50_s, row.latency_p90_s

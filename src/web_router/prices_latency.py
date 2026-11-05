"""The check a price row's measured latency passes: all five fields, or none.

Kept apart from :mod:`web_router.prices` so that module stays small; it takes
the fields as values and knows nothing of :class:`~web_router.prices.Price`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from web_router.constants_prices import MSG_PRICE_LATENCY


def check_latency(
    p50: float | None,
    p90: float | None,
    n: int | None,
    runs: Sequence[str] | None,
    measured: date | None,
    key: tuple[str, str, str | None],
) -> None:
    """Raise ``ValueError`` unless the fields are all ``None`` or whole: ``0 <= p50 <= p90``, ``n >= 1``, runs named."""
    if all(value is None for value in (p50, p90, n, runs, measured)):
        return
    if p50 is None or p90 is None or n is None or not runs or measured is None or not 0 <= p50 <= p90 or n < 1:
        raise ValueError(MSG_PRICE_LATENCY.format(key=key))

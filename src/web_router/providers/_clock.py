"""Today's date for options counted back from it, and a way to pin it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

from web_router.types_options import Recency

RECENCY_DAYS: dict[Recency, int] = {"day": 1, "week": 7, "month": 30, "year": 365}
"""How many days back each ``recency`` window reaches, for a backend that takes a start date rather than a window."""

_PINNED_TODAY: ContextVar[date | None] = ContextVar("web_router_pinned_today", default=None)


def today() -> date:
    """Today's date, for an option counted back from it -- ``recency`` on a backend that takes a start date.

    The UTC date, so a window does not move with the caller's time zone. Read
    through here so :func:`pinned_today` can fix it: a provider contract records
    the dates a request sends, and must not go stale overnight.
    """
    return _PINNED_TODAY.get() or datetime.now(timezone.utc).date()


@contextmanager
def pinned_today(day: date) -> Iterator[None]:
    """Make :func:`today` return ``day`` inside the block."""
    token = _PINNED_TODAY.set(day)
    try:
        yield
    finally:
        _PINNED_TODAY.reset(token)


def recency_start(recency: Recency | None) -> date | None:
    """The first day a ``recency`` window covers, counted back from :func:`today`; ``None`` when unset."""
    return today() - timedelta(days=RECENCY_DAYS[recency]) if recency else None

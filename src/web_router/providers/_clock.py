"""Today's date for options counted back from it, and a way to pin it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

_PINNED_TODAY: ContextVar[date | None] = ContextVar("web_router_pinned_today", default=None)


def today() -> date:
    """Today's date, for an option counted back from it -- ``recency`` on a backend that takes a start date.

    Read through here rather than ``date.today()`` so :func:`pinned_today` can
    fix it: a provider contract records the dates a request sends, and must not
    go stale overnight.
    """
    return _PINNED_TODAY.get() or date.today()


@contextmanager
def pinned_today(day: date) -> Iterator[None]:
    """Make :func:`today` return ``day`` inside the block."""
    token = _PINNED_TODAY.set(day)
    try:
        yield
    finally:
        _PINNED_TODAY.reset(token)

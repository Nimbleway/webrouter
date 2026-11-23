"""Defer inferred titles until the outer extract call has screened its members' pages."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_DELEGATED: ContextVar[bool] = ContextVar("web_router_delegated_extract", default=False)


def is_delegated() -> bool:
    """Whether this extract is a member of another extract call."""
    return _DELEGATED.get()


@contextmanager
def delegating() -> Iterator[None]:
    """Keep member titles as supplied by the backend until the outer screening finishes."""
    token = _DELEGATED.set(True)
    try:
        yield
    finally:
        _DELEGATED.reset(token)

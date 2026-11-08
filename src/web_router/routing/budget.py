"""Holding a call to its :class:`~web_router.Budget`.

Experimental. Every method that takes ``budget=`` runs inside
:func:`budget_scope`. Budgets are not enforced yet: a call with one runs
exactly as a call without.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from web_router.routing.contract import Budget


@contextmanager
def budget_scope(budget: Budget | None) -> Iterator[None]:
    """Hold ``budget`` over the call made inside the block. Not enforced yet: the call runs as it would without."""
    del budget  # accepted and passed down; enforcement comes later
    yield

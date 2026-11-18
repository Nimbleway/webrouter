"""The route a call runs in, held in force beside its budget scope while the call runs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from web_router.routing.attempts import CallRoute, in_route
from web_router.routing.budget import BudgetedCall, recorded


@contextmanager
def logged_route(scope: BudgetedCall[Any], route: CallRoute | None) -> Iterator[BudgetedCall[Any]]:
    """``route`` in force beside the budget's hook, and a ledger logging its attempts; nothing for an unrouted call."""
    if route is None:
        yield scope
        return
    with recorded(scope) as logged, in_route(route):
        yield logged

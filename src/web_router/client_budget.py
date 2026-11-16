"""``budget()`` and ``log_spend()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins. The scopes they return live in :mod:`web_router.client_scope`, which
imports the routers, so it is imported on first use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from web_router.client_spend import SpendLogging
from web_router.routing import Budget

if TYPE_CHECKING:
    from web_router.client import AsyncWebRouter, WebRouter
    from web_router.client_scope import AsyncBudgetScope, BudgetScope


class SyncBudget(SpendLogging):
    def budget(self, limits: Budget, /) -> BudgetScope:
        """A budget several calls spend from, together.

        ``with router.budget(Budget(max_cost_usd=3.0, time_limit_s=600)) as b:``
        -- every call made through ``b`` (``b.search(...)``, ``b.research(...)``),
        and every call made on the router inside the block, spends from it. Its
        clock starts now. Each call's budget is the tighter of its own
        ``budget=`` and what is left, and each attempt reserves its estimate
        before it starts, so calls in parallel threads never overspend
        together: one that does not fit raises
        :class:`~web_router.BudgetExceededError` at once. ``b.status()`` reports
        what is spent, reserved and left. A thread started inside the block
        does not see it: call through ``b`` there. See
        :mod:`web_router.routing.pool`.
        """
        from web_router.client_scope import BudgetScope

        return BudgetScope(cast("WebRouter", self), limits)


class AsyncBudget(SpendLogging):
    def budget(self, limits: Budget, /) -> AsyncBudgetScope:
        """As on the sync router, for ``async with``; asyncio tasks started inside the block spend from it."""
        from web_router.client_scope import AsyncBudgetScope

        return AsyncBudgetScope(cast("AsyncWebRouter", self), limits)

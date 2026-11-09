"""Which shared budgets are in force here: the pools a router's calls spend from. Experimental.

Held in a context variable, as the per-call deadline is, so a call made on the
router inside ``with wr.budget(...)`` spends from its pool too (:mod:`.pool`).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_router.routing.pool import BudgetPool

_POOLS: ContextVar[tuple[BudgetPool, ...]] = ContextVar("web_router_budget_pools", default=())
"""The pools in force in this context, innermost last."""


def bind(pool: BudgetPool) -> Token[tuple[BudgetPool, ...]]:
    """Put ``pool`` in force for its router's calls in this context, until :func:`unbind` with the token."""
    return _POOLS.set((*_POOLS.get(), pool))


def unbind(token: Token[tuple[BudgetPool, ...]]) -> None:
    """Put back the pools that were in force before :func:`bind` returned ``token``."""
    _POOLS.reset(token)


def current_pool(owner: object = None) -> BudgetPool | None:
    """The innermost pool ``owner``'s calls spend from in this context -- any router's with no owner -- or ``None``.

    A pool with no owner of its own (the MCP server's, which outlives the routers
    ``load_env`` replaces) applies to every router's calls.
    """
    for pool in reversed(bound()):
        if owner is None or pool.owner is None or pool.owner is owner:
            return pool
    return None


def bound() -> tuple[BudgetPool, ...]:
    """The pools in force in this context, innermost last."""
    return _POOLS.get()

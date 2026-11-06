"""The scopes :meth:`~web_router.WebRouter.budget` returns: a router's budgeted methods, spending from one budget.

Experimental. Each method is the router's own, with the same arguments,
called with the scope's :class:`~web_router.routing.pool.BudgetPool` in force,
so it works from any thread or task. Entering the scope also puts the pool in
force for the calls made on the router itself inside the block.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, Concatenate, ParamSpec, TypeVar, cast

from web_router.client import AsyncWebRouter, WebRouter
from web_router.routing.contract import Budget
from web_router.routing.pool import BudgetPool, BudgetStatus, bind, current_pool, unbind

_P = ParamSpec("_P")
_R = TypeVar("_R")


_ENTRIES: ContextVar[tuple[tuple[_Scope, Token[BudgetPool | None]], ...]] = ContextVar(
    "web_router_budget_scope_entries", default=()
)
"""The scopes entered in this context, each with the token that undoes its binding; per thread and per task."""


class _Scope:
    """What both scopes share: the pool, made where the scope is, and entering and leaving it."""

    def __init__(self, router: Any, budget: Budget) -> None:
        self.router = router
        self.pool = BudgetPool(budget, parent=current_pool())

    def status(self) -> BudgetStatus:
        """What is spent, reserved and left, and the clock: see :class:`~web_router.routing.pool.BudgetStatus`."""
        return self.pool.status()

    def _enter(self) -> None:
        _ENTRIES.set((*_ENTRIES.get(), (self, bind(self.pool))))

    def _exit(self) -> None:
        """Undo this context's latest entry of this scope: another thread or task's entries are its own."""
        entries = _ENTRIES.get()
        index = max(at for at, (scope, _) in enumerate(entries) if scope is self)
        unbind(entries[index][1])
        _ENTRIES.set(entries[:index] + entries[index + 1 :])


def _spending(method: Callable[Concatenate[WebRouter, _P], _R]) -> Callable[Concatenate[BudgetScope, _P], _R]:
    """``method``'s namesake on the scope's router, a subclass's override included, with the pool in force."""
    name = method.__name__

    @functools.wraps(method)
    def call(scope: BudgetScope, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        token = bind(scope.pool)
        try:
            return cast("Callable[_P, _R]", getattr(scope.router, name))(*args, **kwargs)
        finally:
            unbind(token)

    return call


def _aspending(
    method: Callable[Concatenate[AsyncWebRouter, _P], Awaitable[_R]],
) -> Callable[Concatenate[AsyncBudgetScope, _P], Coroutine[Any, Any, _R]]:
    """As :func:`_spending`, awaited."""
    name = method.__name__

    @functools.wraps(method)
    async def call(scope: AsyncBudgetScope, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        token = bind(scope.pool)
        try:
            return await cast("Callable[_P, Awaitable[_R]]", getattr(scope.router, name))(*args, **kwargs)
        finally:
            unbind(token)

    return call


class BudgetScope(_Scope):
    """A :class:`~web_router.WebRouter`'s budgeted methods, all spending from one budget. Experimental."""

    router: WebRouter

    search = _spending(WebRouter.search)
    extract = _spending(WebRouter.extract)
    answer = _spending(WebRouter.answer)
    research = _spending(WebRouter.research)
    browse = _spending(WebRouter.browse)

    def __enter__(self) -> BudgetScope:
        self._enter()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._exit()


class AsyncBudgetScope(_Scope):
    """An :class:`~web_router.AsyncWebRouter`'s budgeted methods, all spending from one budget. Experimental."""

    router: AsyncWebRouter

    search = _aspending(AsyncWebRouter.search)
    extract = _aspending(AsyncWebRouter.extract)
    answer = _aspending(AsyncWebRouter.answer)
    research = _aspending(AsyncWebRouter.research)
    browse = _aspending(AsyncWebRouter.browse)

    async def __aenter__(self) -> AsyncBudgetScope:
        self._enter()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._exit()

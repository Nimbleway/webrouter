"""A routed call: when to ask the attached router, what it is shown, and how its answer orders the call.

Experimental. The routers take :class:`SyncRouted` or :class:`AsyncRouted` as
a mixin, and every method that can be routed -- search, extract, answer,
research and browse -- runs through ``_routed`` / ``_arouted``. The router is
asked only when one is attached, the method is in ``route_tools`` (the
caller's; else the router's own ``route_tools`` attribute; else all five), and the
call goes to ``auto``: a provider the caller names, or a default that is not
``auto``, is a pinned provider and leaves nothing to order. With no router
attached a call runs exactly as it did before routers existed.

The router orders the backends of the method the caller called; it never
changes the method. It is shown the candidates
:func:`~web_router.routing.candidates.build_candidates` finds on the chain the
call runs on, and what it answered is merged by
:func:`~web_router.routing.decide.decided` -- the merge rule, then the
budget's research effort -- and recorded on the response's ``decision``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, TypeVar

from pydantic import BaseModel

from web_router.providers._cooldown import noting
from web_router.providers._runtime import lent
from web_router.routing.budget import budget_scope
from web_router.routing.contract import Budget
from web_router.routing.effort_call import named, with_record
from web_router.routing.in_loop import warn_if_in_loop
from web_router.routing.metered import metered
from web_router.routing.pool import limited
from web_router.routing.route_scope import logged_route
from web_router.routing.router_setup import TOOL_OF, RouterSetup
from web_router.types import NormalizedRequest

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)
_RequestT = TypeVar("_RequestT", bound=NormalizedRequest)


class SyncRouted(RouterSetup):
    def _routed(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], _ResponseT],
    ) -> _ResponseT:
        """Run ``call``, which calls ``chain``, in ``budget``'s scope.

        A routed call runs in the order the router chose, and its response records how. Every response
        carries what the call took and cost (:func:`~.metered.metered`).
        """
        started, tool = monotonic(), TOOL_OF[type(request)]
        warn_if_in_loop(tool)
        request, chosen = named(chain, request, limited(budget, self))  # before the scope, which admits a named one
        with self._spending(tool), lent(self._runtime), budget_scope(budget, tool, chain, request, self) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else self._attached().call(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with logged_route(scope, route) as logged, noting(self._runtime, chain) as noted:
                answered = logged.settle(with_record(noted(call(request)), record or chosen))
        return self._spent(metered(answered, tool, request, started), tool)


class AsyncRouted(RouterSetup):
    async def _arouted(
        self,
        chain: Any,
        request: _RequestT,
        budget: Budget | None,
        call: Callable[[_RequestT], Awaitable[_ResponseT]],
    ) -> _ResponseT:
        """Await ``call``, which calls ``chain``, as :meth:`SyncRouted._routed` runs it."""
        started, tool = monotonic(), TOOL_OF[type(request)]
        request, chosen = named(chain, request, limited(budget, self))  # before the scope, which admits a named one
        with self._spending(tool), lent(self._runtime), budget_scope(budget, tool, chain, request, self) as scope:
            routed = self._route_request(chain, request, scope.budget)
            outcome = None if routed is None else await self._attached().acall(routed[0])
            route, request, record = self._prepared(chain, request, scope.budget, routed, outcome)
            with logged_route(scope, route) as logged, noting(self._runtime, chain) as noted:
                answered = logged.settle(with_record(noted(await call(request)), record or chosen))
        return self._spent(metered(answered, tool, request, started), tool)

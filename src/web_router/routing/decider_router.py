"""``DeciderRouter``: a router made of a decider, the glue every decision API needs written once.

Experimental. ``DeciderRouter(decider)`` is a router like any other: attach it
with ``router=``, or register it by name::

    register_router("mine", lambda **config: DeciderRouter(MyDecider(**config)))

For each routed call it asks the decider one question -- the prompt, the
instructions, and each candidate id with its method, backend, estimated cost
and p90 latency (extract only when the prompt names a URL) -- checks the reply
(:mod:`.decider_reply`) and answers a :class:`~web_router.RouteDecision` whose
``tools`` and ``providers`` follow the ranking. It never sets options,
exclusions or URLs, and its reason holds the decider's name, the first id and
that id's score, never the decider's own words. The decider's ``timeout_s``,
``name`` and ``route_tools`` are the router's. A sync decider runs on a worker
thread from async code; an async one on web-router's private loop from sync
code, as any router does.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from web_router.constants_decider import MSG_DECIDER_FORM
from web_router.exceptions import ConfigurationError
from web_router.routing._forms import _declared_timeout
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.decider import Decider, DeciderQuestion
from web_router.routing.decider_question import decision_for, question_for
from web_router.routing.decider_reply import checked_reply
from web_router.routing.registry import aclose_router, discard_router


def _decider_name(decider: object) -> str:
    declared = getattr(decider, "name", None)
    return declared if isinstance(declared, str) and declared else type(decider).__qualname__


class DeciderRouter:
    """A router that asks a decider one choice question per call and routes by its checked ranking."""

    route: Callable[[RouteRequest], RouteDecision]
    """Set when the decider answers synchronously, so sync callers need no event loop."""

    def __init__(self, decider: Decider, *, owns_decider: bool = True) -> None:
        """
        Args:
            decider: An object with ``decide(question)``, ``adecide(question)`` or both.
            owns_decider: Whether ``close()`` and ``aclose()`` close the decider too. True unless set.

        Raises:
            ConfigurationError: for a decider with neither method, or a ``timeout_s`` that is not a positive
                number of seconds.
        """
        self._decide = getattr(decider, "decide", None)
        self._adecide = getattr(decider, "adecide", None)
        if not callable(self._decide) and not callable(self._adecide):
            raise ConfigurationError(MSG_DECIDER_FORM.format(kind=type(decider).__name__))
        self.decider = decider
        self._owns = owns_decider
        self.timeout_s = _declared_timeout(decider)
        self.router_name = _decider_name(decider)
        self.route_tools = getattr(decider, "route_tools", None)
        if callable(self._decide) and not inspect.iscoroutinefunction(self._decide):
            self.route = self._route

    def __repr__(self) -> str:
        return f"DeciderRouter({self.router_name!r}, timeout_s={self.timeout_s})"

    def _route(self, req: RouteRequest) -> RouteDecision:
        question = question_for(req)
        if not question.options:
            return RouteDecision()
        assert callable(self._decide)  # set only for a sync decider
        return self._decision(req, question, self._decide(question))

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        """Ask the decider: ``adecide`` when it has one, else ``decide`` on a worker thread."""
        question = question_for(req)
        if not question.options:
            return RouteDecision()
        if callable(self._adecide):
            reply = await self._adecide(question)
        else:
            assert callable(self._decide)
            reply = await asyncio.to_thread(self._decide, question)
            if inspect.isawaitable(reply):  # a decide() that answers with a coroutine
                reply = await reply
        return self._decision(req, question, reply)

    def _decision(self, req: RouteRequest, question: DeciderQuestion, reply: Any) -> RouteDecision:
        checked = checked_reply(reply, question.options, self.router_name)
        return decision_for(req, list(checked.ranked), checked.scores, checked.confidence, self.router_name)

    def close(self) -> None:
        """Close the decider it owns: its ``close()``, else its ``aclose()``, awaited before this returns."""
        if self._owns:
            discard_router(self.decider)

    async def aclose(self) -> None:
        """Close the decider it owns: its ``aclose()``, else its ``close()``."""
        if self._owns:
            await aclose_router(self.decider)

"""The built-in ``fallback`` router: a list of routers, asked in order until one answers.

Experimental. Opt in by name::

    WebRouter(router="fallback", router_config={"routers": [
        {"name": "remote", "url": "https://router.example.com/route"},  # a registered name and its config
        my_classifier,                                                  # or any router function or object
    ]})

Each member is attached as ``router=`` attaches one, so it may be sync or
async and keeps its own ``timeout_s`` (50 ms when it declares none). A member
that times out, raises, answers something other than a decision, or answers a
decision that decides nothing is passed over, and the next one is asked, with
the smaller of its own limit and the time left. The first decision that decides
something is the answer; when none does, the answer is empty, recorded as
``router_empty``, and ``fail_on_router_error=True`` raises
:class:`~web_router.RouterError` instead.

Every member asked is sent the prompt, a member that times out after receiving
it included. A member that declares ``route_tools`` is asked only on those
tools. Its own time limit is the sum of its members', unless ``timeout_s`` sets
it. The decision record names it ``fallback(<member>, ...)`` and lists in
``members`` each one asked: its name, its outcome, its milliseconds, and
whether its decision counted. A member passed in is named by its
``router_name``, else ``custom``; one built by name by its ``router_name``, else
its registered name. Members built by name are closed when this router is;
members passed in are the caller's.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Any, cast

from web_router.constants_fallback import FALLBACK_DISPLAY_NAME, ROUTER_OK
from web_router.constants_merge import ROUTER_EMPTY
from web_router.routing.composite import CompositeDecision, composed
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.fallback_members import Member, aclose_members, built_members, close_members
from web_router.routing.invoke import RouterOutcome
from web_router.routing.merge_shape import decides_nothing
from web_router.routing.route_tools import ALL_TOOLS
from web_router.types_decision import DecisionMember, MemberOutcome


class _Asking:
    """One call's walk down the members: the time it has left, and what each member asked came to."""

    def __init__(self, req: RouteRequest) -> None:
        self.req = req
        self.started = time.perf_counter()
        self.members: list[DecisionMember] = []

    def left(self) -> float:
        return self.req.deadline_s - (time.perf_counter() - self.started)

    def skips(self, member: Member) -> bool:
        """Whether ``member`` declares ``route_tools`` that leave out the tool this call is for."""
        return self.req.pinned_tool is not None and self.req.pinned_tool not in member.tools

    def request_for(self, member: Member) -> RouteRequest:
        """The request ``member`` is shown: its deadline the smaller of its own limit and the time left."""
        return replace(self.req, deadline_s=min(member.attached.timeout_s, self.left()))

    def settle(self, member: Member, outcome: RouterOutcome) -> RouteDecision | None:
        """``member``'s decision when it decides something, else ``None``; either way recorded."""
        decision = outcome.decision
        empty = decision is None or decides_nothing(decision)
        code = cast("MemberOutcome", outcome.failure or (ROUTER_EMPTY if empty else ROUTER_OK))
        counted = code == ROUTER_OK
        self.members.append(DecisionMember(name=member.name, outcome=code, ms=outcome.elapsed_ms, counted=counted))
        return decision if counted else None


class FallbackRouter:
    """Routers asked in order until one decides something: what ``router="fallback"`` builds from ``router_config``."""

    def __init__(
        self,
        *,
        routers: Sequence[Any],
        timeout_s: float | None = None,
        route_tools: Collection[str] | None = None,
    ) -> None:
        """
        Args:
            routers: The members, asked in this order: a registered router name, a mapping of a ``"name"`` and
                that router's ``router_config``, or a router function or object. At least one.
            timeout_s: Seconds this router has in all; the sum of its members' limits unless set.
            route_tools: The tools it is consulted on when the caller passes none; unless set, every tool a
                member is asked on.

        Raises:
            ConfigurationError: for ``routers`` empty or not a list, or a member that cannot be built or
                attached; members built by then are closed.
        """
        self._members = built_members(routers)
        self.timeout_s = sum(m.attached.timeout_s for m in self._members) if timeout_s is None else timeout_s
        self.router_name = FALLBACK_DISPLAY_NAME.format(members=", ".join(m.name for m in self._members))
        served = frozenset().union(*(m.tools for m in self._members))
        self.route_tools = route_tools if route_tools is not None or served == ALL_TOOLS else sorted(served)
        self.min_url_policy = next((m.url_policy for m in self._members if m.url_policy), None)

    def __repr__(self) -> str:
        return f"FallbackRouter({self.router_name!r}, timeout_s={self.timeout_s})"

    def route(self, req: RouteRequest) -> CompositeDecision:
        """Ask each member in turn from sync code; the first decision that decides something."""
        asking = _Asking(req)
        for member in self._members:
            if asking.skips(member):
                continue
            if asking.left() <= 0:
                break
            if (decision := asking.settle(member, member.attached.call(asking.request_for(member)))) is not None:
                return composed(decision, asking.members)
        return composed(None, asking.members)

    async def aroute(self, req: RouteRequest) -> CompositeDecision:
        """:meth:`route` from async code."""
        asking = _Asking(req)
        for member in self._members:
            if asking.skips(member):
                continue
            if asking.left() <= 0:
                break
            outcome = await member.attached.acall(asking.request_for(member))
            if (decision := asking.settle(member, outcome)) is not None:
                return composed(decision, asking.members)
        return composed(None, asking.members)

    def close(self) -> None:
        """Close the members built by name; those passed in are the caller's."""
        close_members(self._members)

    async def aclose(self) -> None:
        """:meth:`close` from async code: each member's ``aclose()``, else its ``close()``."""
        await aclose_members(self._members)

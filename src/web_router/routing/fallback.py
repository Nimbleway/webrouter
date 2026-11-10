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
``router_empty`` -- or ``router_timeout`` when the time ran out first -- and
``fail_on_router_error=True`` raises :class:`~web_router.RouterError` with that
code instead. It stops 20 ms before its deadline (``FALLBACK_MARGIN_S``), so its
answer, with the members it asked, always arrives before the call stops waiting.

Every member asked is sent the prompt, a member that times out after receiving
it included. A member that declares ``route_tools`` is asked only on those
tools. Its own time limit is the sum of its members' plus that margin, unless
``timeout_s`` sets it. The decision record names it ``fallback(<member>, ...)`` and lists in
``members`` each one asked: its name, its outcome, its milliseconds, and
whether its decision counted. A member passed in is named by its
``router_name``, else ``custom``; one built by name by its ``router_name``, else
its registered name. Members built by name are closed when this router is;
members passed in are the caller's.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Iterator, Sequence
from dataclasses import replace
from typing import Any, cast

from web_router.constants_fallback import (
    FALLBACK_DISPLAY_NAME,
    FALLBACK_MARGIN_S,
    MSG_FALLBACK_TIMEOUT,
    ROUTER_OK,
)
from web_router.constants_merge import ROUTER_EMPTY, ROUTER_TIMEOUT
from web_router.exceptions import ConfigurationError
from web_router.routing.composite import CompositeDecision, composed, decides_nothing
from web_router.routing.contract import RouteDecision, RouteRequest
from web_router.routing.fallback_members import Member, aclose_members, built_members, close_members
from web_router.routing.invoke import RouterOutcome
from web_router.routing.route_tools import ALL_TOOLS
from web_router.types_decision import DecisionMember, MemberOutcome


class _Asking:
    """One call's walk down the members: the time it has left, and what each member asked came to."""

    def __init__(self, req: RouteRequest) -> None:
        """Start the clock now, when the fallback itself starts.

        A :class:`RouteRequest` carries ``deadline_s``, seconds relative to when the caller started waiting, and
        no absolute start or deadline, and :class:`~.invoke.AttachedRouter` passes none: so this clock starts
        a little after the caller's, by the time it took to start this router -- a worker thread, or a task on
        an event loop, usually well under a millisecond. ``FALLBACK_MARGIN_S`` (20 ms) covers that lag, so the
        answer, members included, arrives before the caller stops waiting; a start delayed by more than the
        margin, under heavy load, can still lose it to the caller's timeout.
        """
        self.req = req
        self.started = time.perf_counter()
        self.members: list[DecisionMember] = []
        self.timed_out = False
        """Whether the walk ended for want of time: the last member asked timed out with no time left after it
        or with all that was left as its limit, or no time was left to ask the next."""
        self.cut = False
        """Whether the member being asked was given the time left, less than its own limit."""

    def left(self) -> float:
        """Seconds left to ask members: the deadline less the margin kept back, less the time spent."""
        return self.req.deadline_s - FALLBACK_MARGIN_S - (time.perf_counter() - self.started)

    def out_of_time(self) -> bool:
        """Whether no time is left to ask the next member; recorded as the walk timing out."""
        if self.left() <= 0:
            self.timed_out = True
        return self.timed_out

    def skips(self, member: Member) -> bool:
        """Whether ``member`` declares ``route_tools`` that leave out the tool this call is for."""
        return self.req.pinned_tool is not None and self.req.pinned_tool not in member.tools

    def to_ask(self, members: Sequence[Member]) -> Iterator[Member]:
        """Each member to ask, in order: one whose ``route_tools`` leave this tool out skipped; none once out of time.

        Time is checked as each is reached, after the one before it has answered.
        """
        for member in members:
            if self.skips(member):
                continue
            if self.out_of_time():
                return
            yield member

    def request_for(self, member: Member) -> RouteRequest:
        """The request ``member`` is shown: its deadline the smaller of its own limit and the time left."""
        left = self.left()
        self.cut = left < member.attached.timeout_s
        return replace(self.req, deadline_s=min(member.attached.timeout_s, left))

    def settle(self, member: Member, outcome: RouterOutcome) -> RouteDecision | None:
        """``member``'s decision when it decides something, else ``None``; either way recorded."""
        decision = outcome.decision
        empty = decision is None or decides_nothing(decision)
        # An inner fallback out of time; one that still decides something is taken as its answer.
        nested = empty and isinstance(decision, CompositeDecision) and decision.timed_out
        settled = ROUTER_TIMEOUT if nested else ROUTER_EMPTY if empty else ROUTER_OK
        code = cast("MemberOutcome", outcome.failure or settled)
        counted = code == ROUTER_OK
        # Out of time when none is left, or the member had all of it and timed out within two margins of the end:
        # an inner fallback stops its own margin early. One that reports its own timeout early leaves the next.
        near_end = self.left() <= 0 or (self.cut and self.left() < 2 * FALLBACK_MARGIN_S)
        self.timed_out = code == ROUTER_TIMEOUT and near_end
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
            timeout_s: Seconds this router has in all; unless set, the sum of its members' limits and the
                20 ms margin it keeps back from its deadline.
            route_tools: The tools it is consulted on when the caller passes none; unless set, every tool a
                member is asked on.

        Raises:
            ConfigurationError: for ``routers`` empty or not a list, a ``timeout_s`` not above the 20 ms margin,
                or a member that cannot be built or attached; members built by then are closed.
        """
        if isinstance(timeout_s, int | float) and not isinstance(timeout_s, bool) and timeout_s <= FALLBACK_MARGIN_S:
            raise ConfigurationError(MSG_FALLBACK_TIMEOUT.format(margin=FALLBACK_MARGIN_S, got=timeout_s))
        self._members = built_members(routers)
        summed = sum(m.attached.timeout_s for m in self._members) + FALLBACK_MARGIN_S
        self.timeout_s = summed if timeout_s is None else timeout_s
        self.router_name = FALLBACK_DISPLAY_NAME.format(members=", ".join(m.name for m in self._members))
        served = frozenset().union(*(m.tools for m in self._members))
        self.route_tools = route_tools if route_tools is not None or served == ALL_TOOLS else sorted(served)
        self.min_url_policy = next((m.url_policy for m in self._members if m.url_policy), None)

    def __repr__(self) -> str:
        return f"FallbackRouter({self.router_name!r}, timeout_s={self.timeout_s})"

    def route(self, req: RouteRequest) -> CompositeDecision:
        """Ask each member in turn from sync code; the first decision that decides something."""
        asking = _Asking(req)
        for member in asking.to_ask(self._members):
            if (decision := asking.settle(member, member.attached.call(asking.request_for(member)))) is not None:
                return composed(decision, asking.members)
        return composed(None, asking.members, timed_out=asking.timed_out)

    async def aroute(self, req: RouteRequest) -> CompositeDecision:
        """:meth:`route` from async code."""
        asking = _Asking(req)
        for member in asking.to_ask(self._members):
            outcome = await member.attached.acall(asking.request_for(member))
            if (decision := asking.settle(member, outcome)) is not None:
                return composed(decision, asking.members)
        return composed(None, asking.members, timed_out=asking.timed_out)

    def close(self) -> None:
        """Close the members built by name; those passed in are the caller's."""
        close_members(self._members)

    async def aclose(self) -> None:
        """:meth:`close` from async code: each member's ``aclose()``, else its ``close()``."""
        await aclose_members(self._members)

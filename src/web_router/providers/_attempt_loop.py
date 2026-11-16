"""One walk down an ``auto`` chain: each member in the call's order, until one answers.

Every ``auto`` method walks its members here, sync and async: search, extract,
answer, research and browse. Before each member the call's hook is asked
(:func:`~web_router.routing.attempts.admitted`), and a member that fails is
recorded (:func:`~web_router.routing.attempts.failed`), which raises when no
other member could do better. What the walks differ in, :class:`Walk` says:

- ``hops``: the one-call chains (search, extract, answer) call each member
  through :func:`~._hop.hop`, which passes over a member while it cools down
  and logs the call, and stop at the call's deadline with the first empty
  answer held. The job chains (research, browse) call the member directly and
  move on only past a failure that proves nothing started: a run or a task
  bills from the moment it was accepted, and a 5xx or a read timeout may come
  after the backend created it.
- ``final``: whether an answer ends the walk; an empty one falls through when
  the chain's ``fallback_on_empty`` says so, and the first is held.
- ``refusals``: failures that pass over a member without counting against it
  (answer's options a member refused before sending).
- ``exhausted``: what the walk returns or raises when no member answered.

A walk that ends on an answer also hands back the member that gave it and the
members after it, where extract retries the URLs that answer failed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from web_router.constants import AUTO_AGGREGATE_FAILURE
from web_router.exceptions import DeadlineExceededError, WebRouterError
from web_router.providers._chain import _RECOVERABLE, _held_or_raise, _nothing_started
from web_router.providers._chain_deadline import _held_or_late, _late_answer
from web_router.providers._hop import ahop, hop
from web_router.routing.attempts import admitted, failed, in_call_order, noted
from web_router.routing.contract import ToolName

_Res = TypeVar("_Res")

Order = list[tuple[int, str]]
"""The members to try, as (chain position, name), in call order."""

Failures = dict[str, WebRouterError]
Exhausted = Callable[[Any, Failures, list[WebRouterError]], Any]
"""Given the first empty answer held, each member's failure and the refusals: what a walk with no answer ends on."""


def _always(response: object) -> bool:
    return True


@dataclass(frozen=True, kw_only=True)
class Walk:
    """How one ``auto`` chain walks its members for one call; see the module docstring."""

    tool: ToolName
    owner: str
    """The ``auto`` provider's name, which its errors carry."""
    member: Callable[[int], Any]
    """Builds, or returns the cached, member at a chain position."""
    method: str
    """The member's method to call, ``search`` or ``submit``; async walks call it with an ``a`` before it."""
    order: Order
    exhausted: Exhausted
    hops: bool = True
    final: Callable[[Any], bool] = _always
    refusals: tuple[type[WebRouterError], ...] = ()
    built: Mapping[int, object] = field(default_factory=dict)
    """The members built so far, by chain position: a failure's says whether it held a credential to refuse."""

    @classmethod
    def over(cls, eligible: Order, **fields: Any) -> Walk:
        """A walk over ``eligible``, in the call's order for its ``tool``."""
        return cls(order=in_call_order(fields["tool"], eligible), **fields)


@dataclass(frozen=True)
class Ran(Generic[_Res]):
    """How a walk ended: the response, and when a member gave it, that member and the members after it."""

    response: _Res
    provider: str = ""
    rest: Order = field(default_factory=list)


@dataclass
class _Tried:
    """What a walk has met so far: each member's failure, the refusals, the first empty answer."""

    failures: Failures = field(default_factory=dict)
    refused: list[WebRouterError] = field(default_factory=list)
    held: Any = None

    def hold(self, response: object) -> None:
        # Not `held or response`: an empty response is falsy, having no length.
        self.held = response if self.held is None else self.held


def answered_unless(empty: Callable[[Any], bool], fallback_on_empty: bool) -> Callable[[Any], bool]:
    """Whether an answer ends the walk: any does, unless ``fallback_on_empty`` and it is ``empty``."""
    return lambda response: not (fallback_on_empty and empty(response))


def held_or_raise(owner: str, template: str = AUTO_AGGREGATE_FAILURE) -> Exhausted:
    """A one-call chain's end: the first empty answer held, else every member's reason in ``template``."""
    return lambda held, failures, refused: _held_or_raise(held, owner, failures, template, refused)


def raising(error: Callable[[Failures], WebRouterError]) -> Exhausted:
    """A job chain's end: ``error`` for the members' failures, raised."""

    def exhausted(held: object, failures: Failures, refused: list[WebRouterError]) -> Any:
        raise error(failures)

    return exhausted


def run(walk: Walk, request: Any) -> Ran[Any]:
    """Walk ``walk``'s members with ``request`` until one answers."""
    tried = _Tried()
    for index, (position, name) in enumerate(walk.order):
        if (late := _late(walk, tried)) is not None:
            return Ran(late)
        if not admitted(walk.tool, name, request):
            continue
        try:
            call = getattr(walk.member(position), walk.method)
            response = hop(walk.tool, name, call, request) if walk.hops else call(request)
        except _deadline(walk) as exc:
            return Ran(_held_or_late(tried.held, walk.owner, tried.failures, exc, name, walk.tool))
        except walk.refusals as exc:
            _refused(walk, tried, name, exc)
            continue
        except _RECOVERABLE as exc:
            _failed(walk, tried, position, name, exc)
            continue
        if walk.final(response):
            return Ran(response, name, walk.order[index + 1 :])
        tried.hold(response)
    return Ran(walk.exhausted(tried.held, tried.failures, tried.refused))


async def arun(walk: Walk, request: Any) -> Ran[Any]:
    """Async counterpart of :func:`run`."""
    tried = _Tried()
    for index, (position, name) in enumerate(walk.order):
        if (late := _late(walk, tried)) is not None:
            return Ran(late)
        if not admitted(walk.tool, name, request):
            continue
        try:
            call = getattr(walk.member(position), f"a{walk.method}")
            response = await (ahop(walk.tool, name, call, request) if walk.hops else call(request))
        except _deadline(walk) as exc:
            return Ran(_held_or_late(tried.held, walk.owner, tried.failures, exc, name, walk.tool))
        except walk.refusals as exc:
            _refused(walk, tried, name, exc)
            continue
        except _RECOVERABLE as exc:
            _failed(walk, tried, position, name, exc)
            continue
        if walk.final(response):
            return Ran(response, name, walk.order[index + 1 :])
        tried.hold(response)
    return Ran(walk.exhausted(tried.held, tried.failures, tried.refused))


def _late(walk: Walk, tried: _Tried) -> Any:
    """For a one-call chain out of time, the first empty answer held, else it raises the deadline; else ``None``."""
    return _late_answer(tried.held, walk.owner, tried.failures) if walk.hops else None


def _deadline(walk: Walk) -> tuple[type[DeadlineExceededError], ...]:
    """What ends a one-call chain at the deadline; a job chain lets it raise as it came."""
    return (DeadlineExceededError,) if walk.hops else ()


def _refused(walk: Walk, tried: _Tried, name: str, exc: WebRouterError) -> None:
    tried.refused.append(exc)
    noted(walk.tool, name, exc)


def _failed(walk: Walk, tried: _Tried, position: int, name: str, exc: WebRouterError) -> None:
    """Record ``name``'s failure and move on; raise it when moving on could bill twice or would not help."""
    if not walk.hops and not _nothing_started(exc):
        raise exc
    failed(tried.failures, walk.tool, name, exc, walk.built.get(position))

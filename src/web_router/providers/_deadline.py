"""The per-call deadline: how it is set, how a call reads what is left, and how a provider stops at it.

Kept apart from :mod:`web_router.providers.base`, which builds on it, so the
transports and helpers that clamp a request to the time left can read it
without importing the provider classes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, TypeVar

from web_router.constants import (
    DEADLINE_TOLERANCE,
    JOB_TIMEOUT_ERROR_SUFFIXES,
    MIN_CALL_BUDGET,
    MIN_JOB_BUDGET,
)
from web_router.exceptions import DeadlineExceededError, ProviderError, WebRouterError

if TYPE_CHECKING:
    from web_router.types import ExtractRequest, ExtractResponse

_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class Deadline:
    """The instant the call in flight must answer by, and the budget that set it.

    ``at`` is on :func:`time.monotonic`'s clock; ``seconds`` is what the budget
    was when it was set, kept so an error can name the deadline the caller
    gave rather than whatever fraction of it was left.
    """

    at: float
    seconds: float

    def left(self) -> float:
        """Seconds until :attr:`at`; zero or less once it has passed."""
        return self.at - time.monotonic()


_DEADLINE: ContextVar[Deadline | None] = ContextVar("web_router_deadline", default=None)
"""When the call currently in flight -- a search, an extract, a research run -- must be given up on.

A context variable rather than a parameter on ``_search``, ``_start`` and the
rest, which is the one place this package reaches for something implicit. The
reason is that the budget is not a property of the *request* -- two callers
can wait different lengths for the same question -- and it has to reach the
HTTP and SDK calls at the bottom without appearing in the signature of every
provider between here and there. Being a ``ContextVar`` rather than an
attribute is what makes it correct under concurrency: one router shared by
several tasks gives each of them its own budget, and an async task inherits
the one belonging to whoever awaited it. A worker thread does not: whoever
hands a call to one runs it in :func:`contextvars.copy_context`.
"""


@contextmanager
def call_deadline(seconds: float | None) -> Iterator[None]:
    """Give every provider call made inside this block at most ``seconds`` from now.

    The one way in for a per-call time limit on any method; experimental, as
    everything is before 1.0. ``None`` adds no limit. A block inside another
    can only tighten the deadline, never extend it: the earlier of the two
    holds, and an error names the budget that set it.

    What happens at the deadline depends on the method. A search, extract,
    template run or answer raises
    :class:`~web_router.exceptions.DeadlineExceededError`; an extract keeps
    the documents that came back in time and reports the URLs that did not as
    failures. ``auto`` tries a next member only while time is left. Research
    and browse wait no longer than the deadline and raise their own timeout
    error, which carries the job id; the call that submits a billed run is
    clamped as any other, and nothing is cut short to leave time for another
    member.
    """
    with _tightened(None if seconds is None else Deadline(at=time.monotonic() + seconds, seconds=seconds)):
        yield


@contextmanager
def job_deadline(deadline: float | None) -> Iterator[None]:
    """Apply the monotonic instant ``deadline`` to every provider call made inside this block.

    As :func:`call_deadline`, for a caller that already holds the instant --
    the job core, which measures its own wait from the same one.
    """
    with _tightened(None if deadline is None else Deadline(at=deadline, seconds=max(0.0, deadline - time.monotonic()))):
        yield


@contextmanager
def _tightened(deadline: Deadline | None) -> Iterator[None]:
    """Hold ``deadline`` for this block, unless the one already in force comes first."""
    current = _DEADLINE.get()
    if deadline is not None and (current is None or deadline.at < current.at):
        current = deadline
    token = _DEADLINE.set(current)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def current_deadline() -> Deadline | None:
    """The deadline in force for the call in flight, or ``None`` when it has none."""
    return _DEADLINE.get()


def job_budget(default: float) -> float:
    """How long a single call may take: the caller's remaining time, or ``default``.

    Never longer than ``default``, so a generous job timeout cannot stretch
    one request beyond what the provider's own timeout allows, and never longer
    than what the caller has left, so a backend that does the whole run inside
    one request -- Perplexity's deep research model -- cannot hold a caller who
    asked for thirty seconds for the two minutes its HTTP timeout permits.

    Floored rather than allowed to reach zero: the waiting loop checks the
    deadline before it polls, so a call reaching here has time left, and a
    timeout of zero means "no timeout" to both httpx and the SDKs -- the exact
    opposite of what is being asked for.
    """
    return _budget(default, MIN_JOB_BUDGET)


def call_budget(default: float) -> float:
    """How long one request of a one-call method may take: ``default``, or less when a deadline leaves less.

    Never more than ``default`` and never more than the time left. Unlike
    :func:`job_budget`, floored only at
    :data:`~web_router.constants.MIN_CALL_BUDGET`, so a transport is never
    handed zero or less, yet never given time past the deadline; a call with
    no time left is refused before it is sent.
    """
    return _budget(default, MIN_CALL_BUDGET)


def _budget(default: float, floor: float) -> float:
    """``default``, or the time left when that is less -- but never below ``floor``."""
    deadline = _DEADLINE.get()
    if deadline is None:
        return default
    return max(floor, min(default, deadline.left()))


# The names the research providers were written against. Aliases rather than
# wrappers, so there is one budget whichever name a provider reads it by.
research_deadline = job_deadline
research_budget = job_budget


@contextmanager
def _job_window(deadline: float | None, timeout: float | None) -> Iterator[tuple[float | None, float | None]]:
    """Hold the monotonic instant ``deadline`` for this block, as :func:`job_deadline` does.

    Yields the deadline a job waits to and the timeout its error names: its
    own, or an enclosing call's when that comes first.
    """
    with job_deadline(deadline):
        current = current_deadline()
        if current is None or (deadline is not None and current.at >= deadline):
            yield deadline, timeout
        else:
            yield current.at, current.seconds


class _DeadlineReached(Exception):
    """:func:`_within`'s own signal that the deadline cut a call off, apart from any timeout the call raised itself."""


async def _within(deadline: Deadline, awaitable: Awaitable[_ResultT]) -> _ResultT:
    """Await ``awaitable``, cancelling it when ``deadline`` arrives.

    The transports clamp every request to the time left as well, but a call
    can make several requests, or sleep between them; this is what holds an
    async call to the deadline whatever it does inside.

    Raises:
        _DeadlineReached: the deadline arrived first, with the cancellation's
            ``TimeoutError`` as its cause.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=max(0.0, deadline.left()))
    except asyncio.TimeoutError as exc:
        # From 3.11 wait_for's own timeout and one the call raised itself are
        # the same class; only the clock tells them apart.
        if deadline.left() > 0:
            raise
        raise _DeadlineReached from exc


def _is_timeout(error: ProviderError) -> bool:
    """Whether a provider's failure was a request that timed out, anywhere down its chain of causes."""
    return _timed_out(error.original or error.__cause__)


def _timed_out(exc: BaseException | None) -> bool:
    """Whether ``exc``, or anything down its chain of causes, is a request that timed out."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if any(cls.__name__.endswith(JOB_TIMEOUT_ERROR_SUFFIXES) for cls in type(current).__mro__):
            return True
        current = current.__cause__ or current.__context__
    return False


class _DeadlineMixin:
    """How a provider holds one call to the deadline in force; every provider's base class mixes it in."""

    name: ClassVar[str]

    if TYPE_CHECKING:

        def _wrapped(self, call: Callable[[], _ResultT], message: str, **fields: Any) -> _ResultT: ...

        async def _awrapped(self, awaitable: Awaitable[_ResultT], message: str, **fields: Any) -> _ResultT: ...

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse: ...

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse: ...

    def _bounded(self, call: Callable[[], _ResultT], message: str, **fields: Any) -> _ResultT:
        """:meth:`_wrapped` under the deadline in force: not started once it has passed, and a timeout at it named so.

        A call that answers in one request -- a search, a template run, an
        answer -- goes through here. Its transport clamps each request to the
        time left (see :func:`job_budget`), so a request that stalls fails as
        the deadline arrives, and that failure is raised as
        :class:`DeadlineExceededError` rather than as the backend's own read
        timeout.
        """
        deadline = current_deadline()
        if deadline is None:
            return self._wrapped(call, message, **fields)
        self._refuse_late(deadline)
        try:
            return self._wrapped(call, message, **fields)
        except Exception as exc:
            self._reraise_late(deadline, exc)

    async def _abounded(self, call: Callable[[], Awaitable[_ResultT]], message: str, **fields: Any) -> _ResultT:
        """Async counterpart of :meth:`_bounded`, which also cancels the call at the deadline.

        Takes a callable rather than an awaitable, so a call refused before it
        starts never creates a coroutine nobody awaits.
        """
        deadline = current_deadline()
        if deadline is None:
            return await self._awrapped(call(), message, **fields)
        self._refuse_late(deadline)
        try:
            return await _within(deadline, self._awrapped(call(), message, **fields))
        except Exception as exc:
            self._reraise_late(deadline, exc)

    def _refuse_past_deadline(self) -> None:
        """Raise :class:`DeadlineExceededError` when a deadline in force has already passed.

        Before a step a clamp alone cannot hold: AgentCore's next ``tools/list``
        page, each clamped but never below the floor a timeout needs, or a
        research or browse run, whose start is billed.
        """
        deadline = current_deadline()
        if deadline is not None:
            self._refuse_late(deadline)

    @contextmanager
    def _submit_window(
        self, deadline: float | None, timeout: float | None
    ) -> Iterator[tuple[float | None, float | None]]:
        """:func:`_job_window` for a run about to be submitted, refused when an enclosing deadline has passed.

        Checked before the run's own deadline is held, so only the caller's
        refuses it: a run's own timeout, however short, still submits it.
        """
        self._refuse_past_deadline()
        with _job_window(deadline, timeout) as bound:
            yield bound

    def _refuse_late(self, deadline: Deadline) -> None:
        """Raise :class:`DeadlineExceededError` for a call about to start after its deadline."""
        if deadline.left() <= 0:
            raise DeadlineExceededError(self.name, deadline.seconds)

    def _at_deadline(self, deadline: Deadline, exc: Exception) -> DeadlineExceededError | None:
        """What a failed call under ``deadline`` raises instead of ``exc``, or ``None`` to raise ``exc`` itself.

        The one place a deadline is told from a failure: :func:`_within`
        cutting the call off, or a request timing out at the deadline, is
        :class:`DeadlineExceededError`, its ``__cause__`` the timeout; a
        router error of another kind, the deadline's own included, stands.
        """
        if isinstance(exc, _DeadlineReached):
            late = DeadlineExceededError(self.name, deadline.seconds)
            late.__cause__ = exc.__cause__
            return late
        if isinstance(exc, WebRouterError) and not isinstance(exc, ProviderError):
            return None
        return self._late(deadline, exc)

    def _reraise_late(self, deadline: Deadline, exc: Exception) -> NoReturn:
        """Raise what a call that failed with ``exc`` under ``deadline`` amounts to: the deadline's error or ``exc``."""
        late = self._at_deadline(deadline, exc)
        if late is None:
            raise exc
        raise late from late.__cause__

    def _late(self, deadline: Deadline | None, exc: BaseException) -> DeadlineExceededError | None:
        """The deadline error ``exc`` amounts to, or ``None`` when it is the backend's own failure.

        Only a timeout the deadline set counts: one that fired as the deadline
        arrived, within :data:`~web_router.constants.DEADLINE_TOLERANCE`. A
        request clamped to the time left times out then; one that timed out
        earlier ran into the backend's own, shorter timeout, which is its
        failure -- and the difference between "try another backend" and
        "there is no time to".
        """
        if deadline is None or deadline.left() > DEADLINE_TOLERANCE:
            return None
        if not (_is_timeout(exc) if isinstance(exc, ProviderError) else _timed_out(exc)):
            return None
        late = DeadlineExceededError(self.name, deadline.seconds)
        late.__cause__ = exc
        return late

    def _wait_to_start(self, deadline: Deadline | None, wait: float) -> None:
        """Raise :class:`DeadlineExceededError` for a call whose rate-limit wait would reach the deadline.

        Never sleeps past it: a call that cannot start in time is a failure of
        its URLs now, rather than a sleep that holds the whole extract after
        the deadline.
        """
        if deadline is not None and wait > 0 and wait >= deadline.left():
            raise DeadlineExceededError(self.name, deadline.seconds)

    def _extract_within(self, deadline: Deadline | None, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        """One call of an extract's ``extract``: refused past ``deadline``, and a timeout at it is the deadline's."""
        if deadline is None:
            return self._extract(request, urls)
        self._refuse_late(deadline)
        try:
            return self._extract(request, urls)
        except Exception as exc:
            self._reraise_late(deadline, exc)

    async def _aextract_within(
        self, deadline: Deadline | None, request: ExtractRequest, urls: list[str]
    ) -> ExtractResponse:
        """One call of an extract's ``aextract``, cancelled at ``deadline``; a timeout at it raised as the deadline's.

        Told apart here, as the call fails, not once every call has: by then
        the deadline has passed for all of them, and a backend that refused
        early would read as one the deadline cut off.
        """
        if deadline is None:
            return await self._aextract(request, urls)
        self._refuse_late(deadline)
        try:
            return await _within(deadline, self._aextract(request, urls))
        except Exception as exc:
            self._reraise_late(deadline, exc)

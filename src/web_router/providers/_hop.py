"""One ``auto`` hop: a member's call, passed over while it cools down, timed and logged.

Every one-call ``auto`` chain -- search, extract, answer -- calls its members
through :func:`hop` or :func:`ahop`. The job chains (research, browse) do not:
a run is billed once accepted, and they move on only past a proven refusal.
Before the call, a member the router's cooldowns hold is not called: the hop
raises a ``rate_limit`` :class:`~web_router.exceptions.ProviderError` naming
the time left, which the chain records and moves past like any refusal. After
it, a 429 or 402 starts that member's cooldown (and, when the router asked, a
timeout counts towards one), and the hop is logged on the
``web_router`` logger -- the method, the backend, how it ended, how long it
took and, for an extract, the hosts it was asked for: never a key, a path or a
query string. A success is DEBUG; a failure, a skip and a cooldown are INFO.

Each hop but the last one left is capped: at the router's ``hop_timeout``
seconds, or, with none set, at :data:`~web_router.constants_production.HOP_SHARE`
of the call's time left when it has a deadline (``hop_timeout=False`` caps
nothing). A hop still in flight at its cap fails as a ``timeout``
:class:`~web_router.exceptions.ProviderError`, which the chain moves past like
any other failure, so one backend that hangs no longer takes the whole
deadline. The last hop gets all the time left; past the call's own deadline,
the deadline's error stands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal, TypeVar

from web_router._urls import host_of
from web_router.constants import DEADLINE_TOLERANCE
from web_router.constants_production import (
    HOP_SHARE,
    LOG_HOP_CANCELLED,
    LOG_HOP_COOLING,
    LOG_HOP_CUT_OFF,
    LOG_HOP_FAILED,
    LOG_HOP_OK,
    LOG_HOST_SUFFIX,
    LOG_MORE_HOSTS,
    MSG_HOP_TIMED_OUT,
)
from web_router.exceptions import DeadlineExceededError
from web_router.providers._cooldown_timeouts import HopTimeoutError
from web_router.providers._deadline import Deadline, call_deadline, current_deadline
from web_router.providers._in_flight import in_flight
from web_router.providers._runtime import current_runtime

logger = logging.getLogger(__name__)

_ResultT = TypeVar("_ResultT")
_RequestT = TypeVar("_RequestT")


def hop(
    tool: str, provider: str, call: Callable[[_RequestT], _ResultT], request: _RequestT, last: bool = False
) -> _ResultT:
    """``call(request)``, one member of ``tool``'s chain, unless it is cooling down; log how it went.

    ``last``: no member is left after it, so it is not capped.
    """
    _refuse_cooling(tool, provider, last)
    started = time.monotonic()
    try:
        with _capped(provider, last):
            response = call(request)
    except Exception as exc:
        _failed(tool, provider, exc, started, request)
        raise
    _answered(provider, response)
    logger.debug(LOG_HOP_OK, tool, provider, time.monotonic() - started, _hosts(request))
    return response


async def ahop(
    tool: str,
    provider: str,
    call: Callable[[_RequestT], Awaitable[_ResultT]],
    request: _RequestT,
    last: bool = False,
) -> _ResultT:
    """Async counterpart of :func:`hop`."""
    _refuse_cooling(tool, provider, last)
    flight = in_flight()
    if flight is not None:
        flight.member = provider
    started = time.monotonic()
    try:
        with _capped(provider, last):
            response = await call(request)
    except Exception as exc:
        _failed(tool, provider, exc, started, request)
        raise
    except asyncio.CancelledError:  # cut off: by the deadline when it has passed, else by whoever cancelled the call
        deadline = current_deadline()
        cause = LOG_HOP_CUT_OFF if deadline is not None and deadline.left() <= 0 else LOG_HOP_CANCELLED
        logger.info(LOG_HOP_FAILED, tool, provider, cause, None, time.monotonic() - started, _hosts(request))
        raise
    if flight is not None:
        flight.member = None
    _answered(provider, response)
    logger.debug(LOG_HOP_OK, tool, provider, time.monotonic() - started, _hosts(request))
    return response


@contextmanager
def _capped(provider: str, last: bool) -> Iterator[None]:
    """Hold the block to its hop's cap; a hop it cut off, with call time left, fails as ``timeout``."""
    outer = current_deadline()
    runtime = current_runtime()
    hop_timeout = False if runtime is None else runtime.hop_timeout
    seconds = None if last else _cap(hop_timeout, outer)
    if seconds is None:
        yield
        return
    try:
        with call_deadline(seconds):
            yield
    except DeadlineExceededError as exc:
        if outer is not None and outer.left() <= DEADLINE_TOLERANCE:
            raise  # the call's own deadline: no time left for another member
        message = MSG_HOP_TIMED_OUT.format(seconds=seconds)
        raise HopTimeoutError(provider, message, original=exc, fixed=hop_timeout is not None) from exc


def _cap(hop_timeout: float | Literal[False] | None, outer: Deadline | None) -> float | None:
    """The router's ``hop_timeout``, else its share of the time ``outer`` leaves; ``None`` for no cap."""
    if hop_timeout is False:
        return None
    if hop_timeout is not None:
        return hop_timeout
    return None if outer is None else max(0.0, outer.left()) * HOP_SHARE


def _answered(provider: str, response: object) -> None:
    runtime = current_runtime()
    if runtime is not None:
        runtime.cooldowns.note_answer(provider, response)


def _refuse_cooling(tool: str, provider: str, last: bool) -> None:
    runtime = current_runtime()
    refusal = None if runtime is None else runtime.cooldowns.refusal(provider, last)
    if refusal is not None:
        logger.info(LOG_HOP_COOLING, tool, provider, refusal.retry_after)
        if (flight := in_flight()) is not None:
            flight.failures[provider] = refusal
        raise refusal


def _failed(tool: str, provider: str, exc: Exception, started: float, request: Any) -> None:
    flight = in_flight()
    if flight is not None and flight.member == provider:
        flight.member = None
        flight.failures[provider] = exc
    runtime = current_runtime()
    if runtime is not None:
        runtime.cooldowns.note(provider, exc)
    kind, status = getattr(exc, "kind", type(exc).__name__), getattr(exc, "status_code", None)
    logger.info(LOG_HOP_FAILED, tool, provider, kind, status, time.monotonic() - started, _hosts(request))


def _hosts(request: Any) -> str:
    """`` for https://host`` for an extract's URLs -- the first host, and how many more -- else nothing."""
    urls = getattr(request, "urls", None)
    if not urls:
        return ""
    hosts = list(dict.fromkeys(host_of(url) for url in urls))
    shown = hosts[0] if len(hosts) == 1 else LOG_MORE_HOSTS % (hosts[0], len(hosts) - 1)
    return LOG_HOST_SUFFIX % shown

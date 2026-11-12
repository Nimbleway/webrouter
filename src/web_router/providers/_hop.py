"""One ``auto`` hop: a member's call, passed over while it cools down, timed and logged.

Every one-call ``auto`` chain -- search, extract, answer -- calls its members
through :func:`hop` or :func:`ahop`. The job chains (research, browse) do not:
a run is billed once accepted, and they move on only past a proven refusal.
Before the call, a member the router's cooldowns hold is not called: the hop
raises a ``rate_limit`` :class:`~web_router.exceptions.ProviderError` naming
the time left, which the chain records and moves past like any refusal. After
it, a 429 or 402 starts that member's cooldown, and the hop is logged on the
``web_router`` logger -- the method, the backend, how it ended, how long it
took and, for an extract, the hosts it was asked for: never a key, a path or a
query string. A success is DEBUG; a failure, a skip and a cooldown are INFO.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from web_router._urls import host_of
from web_router.constants_production import (
    LOG_HOP_COOLING,
    LOG_HOP_FAILED,
    LOG_HOP_OK,
    LOG_HOST_SUFFIX,
    LOG_MORE_HOSTS,
)
from web_router.providers._in_flight import in_flight
from web_router.providers._runtime import current_runtime

logger = logging.getLogger(__name__)

_ResultT = TypeVar("_ResultT")
_RequestT = TypeVar("_RequestT")


def hop(tool: str, provider: str, call: Callable[[_RequestT], _ResultT], request: _RequestT) -> _ResultT:
    """``call(request)``, one member of ``tool``'s chain, unless it is cooling down; log how it went."""
    _refuse_cooling(tool, provider)
    started = time.monotonic()
    try:
        response = call(request)
    except Exception as exc:
        _failed(tool, provider, exc, started, request)
        raise
    logger.debug(LOG_HOP_OK, tool, provider, time.monotonic() - started, _hosts(request))
    return response


async def ahop(
    tool: str, provider: str, call: Callable[[_RequestT], Awaitable[_ResultT]], request: _RequestT
) -> _ResultT:
    """Async counterpart of :func:`hop`."""
    _refuse_cooling(tool, provider)
    flight = in_flight()
    if flight is not None:
        flight.member = provider
    started = time.monotonic()
    try:
        response = await call(request)
    except Exception as exc:
        _failed(tool, provider, exc, started, request)
        raise
    if flight is not None:
        flight.member = None
    logger.debug(LOG_HOP_OK, tool, provider, time.monotonic() - started, _hosts(request))
    return response


def _refuse_cooling(tool: str, provider: str) -> None:
    runtime = current_runtime()
    refusal = None if runtime is None else runtime.cooldowns.refusal(provider)
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

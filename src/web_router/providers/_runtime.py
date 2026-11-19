"""What a router lends the calls it makes: its cooldowns, its cap on vendor calls in flight, and its hop timeout.

Held in a context variable for the length of each routed call, as the deadline
is, so the providers underneath read it without it appearing in their
signatures; a provider used on its own, outside any router, sees none and
behaves as it always has. The cap itself is :class:`~web_router.providers._limiter.Limiter`.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from web_router.constants import DELEGATING_PROVIDERS
from web_router.constants_production import MSG_INVALID_HOP_TIMEOUT
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._cooldown import Cooldowns
from web_router.providers._limiter import Limiter, checked_limit

if TYPE_CHECKING:
    from web_router.providers._deadline import Deadline


@dataclass(frozen=True)
class Runtime:
    """One router's cooldowns, cap and hop timeout, lent to the calls it makes."""

    cooldowns: Cooldowns = field(default_factory=Cooldowns)
    limiter: Limiter | None = None
    hop_timeout: float | Literal[False] | None = None
    """The most one ``auto`` hop may take before the chain moves on (:mod:`._hop`): ``None`` for a share of the
    call's time left, ``False`` for no cap."""

    @classmethod
    def capped(
        cls,
        max_concurrency: int | None,
        hop_timeout: float | Literal[False] | None = None,
        timeout_cooldown: bool = True,
    ) -> Runtime:
        """A router's runtime, its vendor calls held to ``max_concurrency`` (``None`` caps nothing), each ``auto``
        hop to ``hop_timeout``, and with ``timeout_cooldown`` a cooldown after timeouts in a row."""
        return cls(
            cooldowns=Cooldowns(timeouts=timeout_cooldown),
            limiter=checked_limit(max_concurrency),
            hop_timeout=checked_hop_timeout(hop_timeout),
        )


def checked_hop_timeout(value: float | Literal[False] | None) -> float | Literal[False] | None:
    """``hop_timeout`` as given: a finite number of seconds above 0, ``None`` (a share of the time left) or ``False``.

    Raises:
        InvalidParameterError: for anything else, ``True`` included.
    """
    if value is None or value is False:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise InvalidParameterError("hop_timeout", MSG_INVALID_HOP_TIMEOUT.format(value=value))
    return float(value)


_RUNTIME: ContextVar[Runtime | None] = ContextVar("web_router_runtime", default=None)
_HOLDING: ContextVar[bool] = ContextVar("web_router_holding_slot", default=False)


@contextmanager
def running(runtime: Runtime) -> Iterator[None]:
    """Lend ``runtime`` to the calls made inside the block."""
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def lent(runtime: Runtime | None) -> AbstractContextManager[None]:
    """:func:`running` for a router's runtime; nothing for a router that has none."""
    return nullcontext() if runtime is None else running(runtime)


def current_runtime() -> Runtime | None:
    """The runtime of the router whose call is in flight, or ``None`` outside any router."""
    return _RUNTIME.get()


def _limiter_for(provider: str) -> Limiter | None:
    """The cap ``provider``'s call is held to: none for a delegating provider, or a call already holding a slot."""
    runtime = _RUNTIME.get()
    if runtime is None or runtime.limiter is None or provider in DELEGATING_PROVIDERS or _HOLDING.get():
        return None
    return runtime.limiter


@contextmanager
def slot(provider: str, deadline: Deadline | None) -> Iterator[None]:
    """Hold one of the router's slots while ``provider`` calls its vendor, waiting no later than ``deadline``."""
    limiter = _limiter_for(provider)
    if limiter is None:
        yield
        return
    with limiter.held(provider, deadline), _holding():
        yield


@asynccontextmanager
async def aslot(provider: str, deadline: Deadline | None) -> AsyncIterator[None]:
    """Async counterpart of :func:`slot`."""
    limiter = _limiter_for(provider)
    if limiter is None:
        yield
        return
    async with limiter.aheld(provider, deadline):
        with _holding():
            yield


@contextmanager
def _holding() -> Iterator[None]:
    """Mark the calls inside as already holding a slot, so a nested vendor call takes no second one."""
    token = _HOLDING.set(True)
    try:
        yield
    finally:
        _HOLDING.reset(token)

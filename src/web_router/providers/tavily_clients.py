"""The Tavily SDK clients both Tavily classes hold, each built the first time a call needs it.

A sync router never awaits, and an async one never blocks, so building both
clients up front left one of them unused on every router -- the async router
held a ``requests`` session it never sent on. Each is built under a lock, so
two threads making a router's first call at once share one client.

The SDK raises its own exceptions for a refused key, a spent quota or a
refused request, with no status on them; :func:`sdk_errors` turns each into a
:class:`~web_router.ProviderError` of the kind its status would have given.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

from tavily import AsyncTavilyClient, TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    KeylessUnsupportedEndpointError,
    MissingAPIKeyError,
    TavilyKeylessLimitError,
    UsageLimitExceededError,
)

from web_router.constants_tavily import TAVILY_LIMIT_MARKER
from web_router.exceptions import ProviderError, describe
from web_router.providers._utils import WarmClients, compact
from web_router.types_errors import ERROR_KIND_AUTH, ERROR_KIND_BAD_REQUEST, ERROR_KIND_RATE_LIMIT, ProviderErrorKind

_KIND_BY_SDK_ERROR: Final[tuple[tuple[type[Exception], ProviderErrorKind], ...]] = (
    (InvalidAPIKeyError, ERROR_KIND_AUTH),  # 401
    (MissingAPIKeyError, ERROR_KIND_AUTH),
    (UsageLimitExceededError, ERROR_KIND_RATE_LIMIT),  # 429, and a keyless call's quota (its subclass)
    (BadRequestError, ERROR_KIND_BAD_REQUEST),  # 400
    (KeylessUnsupportedEndpointError, ERROR_KIND_BAD_REQUEST),
)
"""The kind of each SDK exception that stands for one HTTP status. ``ForbiddenError`` is read by :func:`_kind`."""


def _kind(exc: Exception) -> ProviderErrorKind | None:
    """``exc``'s kind, when it is one of the SDK's own; ``ForbiddenError`` covers a 403 and the plan limits 432/433."""
    if isinstance(exc, ForbiddenError):
        return ERROR_KIND_RATE_LIMIT if TAVILY_LIMIT_MARKER in str(exc).lower() else ERROR_KIND_AUTH
    return next((kind for sdk_type, kind in _KIND_BY_SDK_ERROR if isinstance(exc, sdk_type)), None)


@contextmanager
def sdk_errors(provider: str, message: str) -> Iterator[None]:
    """Raise the SDK's own exceptions as a ``ProviderError`` of their kind, saying ``message``; others pass through."""
    try:
        yield
    except Exception as exc:
        kind = _kind(exc)
        if kind is None:
            raise
        error = ProviderError(provider, message.format(error=describe(exc)), original=exc, kind=kind)
        if isinstance(exc, TavilyKeylessLimitError) and exc.retry_after_seconds is not None:
            error.retry_after = float(exc.retry_after_seconds)
        raise error from exc


class TavilyClients:
    """The SDK clients' construction arguments, and the clients built from them so far."""

    def __init__(self, key: str | None, api_base_url: str | None, kwargs: dict[str, Any]) -> None:
        shared = compact({"api_base_url": api_base_url, **kwargs})
        session, client = shared.pop("session", None), shared.pop("client", None)
        # api_key=None is what puts the SDK in keyless mode; it is not the same
        # as omitting the argument, so pass it through either way.
        self._sync_kwargs = {"api_key": key, **shared, **compact({"session": session})}
        self._async_kwargs = {"api_key": key, **shared, **compact({"client": client})}
        self._sync: TavilyClient | None = None
        self._async: AsyncTavilyClient | None = None
        self._lock = threading.Lock()

    @property
    def sync(self) -> TavilyClient:
        with self._lock:
            if self._sync is None:
                self._sync = TavilyClient(**self._sync_kwargs)
            return self._sync

    @property
    def aio(self) -> AsyncTavilyClient:
        with self._lock:
            if self._async is None:
                self._async = AsyncTavilyClient(**self._async_kwargs)
            return self._async


class TavilyClientsMixin(WarmClients):
    """``_client`` and ``_async_client`` on a Tavily provider, built when first read, and closed if they were."""

    _clients: TavilyClients

    @property
    def _client(self) -> TavilyClient:
        return self._clients.sync

    @property
    def _async_client(self) -> AsyncTavilyClient:
        return self._clients.aio

    def close(self) -> None:
        if self._clients._sync is not None:
            self._clients._sync.close()

    async def aclose(self) -> None:
        if self._clients._async is not None:
            await self._clients._async.close()

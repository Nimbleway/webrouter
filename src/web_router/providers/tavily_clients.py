"""The Tavily SDK clients both Tavily classes hold, each built the first time a call needs it.

A sync router never awaits, and an async one never blocks, so building both
clients up front left one of them unused on every router -- the async router
held a ``requests`` session it never sent on. Each is built under a lock, so
two threads making a router's first call at once share one client.
"""

from __future__ import annotations

import threading
from typing import Any

from tavily import AsyncTavilyClient, TavilyClient

from web_router.providers._utils import compact


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


class TavilyClientsMixin:
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

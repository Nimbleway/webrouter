"""The base for live data backends that talk to a REST API directly: Apify, Bright Data, Oxylabs.

Kept out of :mod:`web_router.providers._http`, which is at its size limit.
:class:`HttpLiveDataProvider` adds two things to the shared HTTP base: one
request helper pair that returns whatever JSON the backend answered -- an
object or, as Apify's dataset items are, an array -- and the translation of a
404 for a source name into :class:`~web_router.LiveDataSourceNotFoundError`.
A subclass is left with the vendor's own paths, payloads and shapes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import httpx

from web_router.constants_live_data import LIVE_DATA_HTTP_TIMEOUT
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers._http import _HttpProvider
from web_router.providers._utils import compact
from web_router.providers.base import LiveDataProvider


class HttpLiveDataProvider(_HttpProvider, LiveDataProvider):
    """A live data backend over plain HTTP: list, describe and run are each one request."""

    not_found_statuses: ClassVar[frozenset[int]] = frozenset({httpx.codes.NOT_FOUND})
    """Statuses that mean "no source by that name" on a describe or a run; overridden where a vendor differs."""

    def __init__(self, *, timeout: float = LIVE_DATA_HTTP_TIMEOUT, **kwargs: Any) -> None:
        """
        Args:
            timeout: Seconds per request. A run scrapes the site before it
                answers, so the default allows the longest synchronous run any
                backend here documents, rather than a search's few seconds.
        """
        super().__init__(timeout=timeout, **kwargs)

    def _send(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        """One request; the decoded JSON body, object or array, or ``None`` for an empty one."""
        response = self._client.request(method, path, params=compact(params or {}), json=json, **self._clamped())
        return self._decoded(response)

    async def _asend(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        """Async counterpart of :meth:`_send`."""
        response = await self._async_client.request(
            method, path, params=compact(params or {}), json=json, **self._clamped()
        )
        return self._decoded(response)

    def _decoded(self, response: httpx.Response) -> Any:
        self._raise_for_status(response)
        return response.json() if response.content else None

    @contextmanager
    def _named(self, source: str) -> Iterator[None]:
        """Turn the backend's "no such source" status into :class:`LiveDataSourceNotFoundError`."""
        try:
            yield
        except ProviderError as exc:
            original = exc.original
            if isinstance(original, httpx.HTTPStatusError) and original.response.status_code in self.not_found_statuses:
                raise LiveDataSourceNotFoundError(self.name, source, original=original) from original
            raise

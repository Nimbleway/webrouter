"""``search()`` and ``extract()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.search`` and ``router.extract`` are where they always were.

``timeout=`` on both is the shortcut for ``budget=Budget(time_limit_s=...)``:
the call waits at most that many seconds, ``auto`` starts a member only while
time is left and passes over one whose measured p90 would not fit, and an
extract keeps every page that came back in time, reporting the rest as
failures. Given beside a ``budget``, the tighter time cap holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from web_router.client_requests import build_extract_request, build_search_request, with_timeout
from web_router.providers.base import ExtractProvider, SearchProvider
from web_router.routing import Budget
from web_router.routing.routed import AsyncRouted, SyncRouted
from web_router.types import (
    ExtractFormat,
    ExtractResponse,
    Provider,
    Recency,
    ResearchEffort,
    SearchDepth,
    SearchResponse,
)


class _CoreLookup:
    """The two lookups the methods here call, which the routers' shared base defines."""

    if TYPE_CHECKING:

        def get_provider(self, name: str | Provider | SearchProvider | None = None) -> SearchProvider: ...

        def get_extract_provider(self, name: str | Provider | ExtractProvider | None = None) -> ExtractProvider: ...


class SyncCore(_CoreLookup, SyncRouted):
    def search(
        self,
        query: str,
        *,
        provider: str | Provider | SearchProvider | None = None,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
        provider_options: dict[str, Any] | None = None,
        timeout: float | None = None,
        budget: Budget | None = None,
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default).

        ``timeout`` caps the seconds the call waits, as ``Budget(time_limit_s=...)``
        does; running out raises :class:`~web_router.DeadlineExceededError`.
        """
        request = build_search_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            recency=recency,
            effort=effort,
            provider_options=provider_options,
        )
        chain = self.get_provider(provider)
        return self._routed(chain, request, with_timeout(budget, timeout), chain.search)

    def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        profile: str | None = None,
        provider_options: dict[str, Any] | None = None,
        timeout: float | None = None,
        budget: Budget | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Several URLs can partly succeed: what came back is in
        :attr:`~web_router.ExtractResponse.documents` and everything that did
        not is in :attr:`~web_router.ExtractResponse.failures`. A call where
        nothing came back raises instead. ``profile`` names a saved identity on
        the backend -- the one a browser session logged into -- whose cookies
        are sent with each fetch; a backend without saved profiles refuses it.

        ``timeout`` caps the seconds the call waits, as ``Budget(time_limit_s=...)``
        does: each vendor call is held to the time left (the built-in backends
        clamp every request to it), the pages that came back in time are
        returned and the rest are failures; when none came back it raises
        :class:`~web_router.DeadlineExceededError`.
        """
        request = build_extract_request(urls, format=format, profile=profile, provider_options=provider_options)
        chain = self.get_extract_provider(provider)
        return self._routed(chain, request, with_timeout(budget, timeout), chain.extract)


class AsyncCore(_CoreLookup, AsyncRouted):
    async def search(
        self,
        query: str,
        *,
        provider: str | Provider | SearchProvider | None = None,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
        provider_options: dict[str, Any] | None = None,
        timeout: float | None = None,
        budget: Budget | None = None,
    ) -> SearchResponse:
        """Search the web through ``provider`` (or the router's default), with ``timeout`` as on the sync router.

        Prefer ``timeout`` to wrapping the call in :func:`asyncio.wait_for`: it
        cuts each vendor call off cleanly, closing its connection.
        """
        request = build_search_request(
            query,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            locale=locale,
            recency=recency,
            effort=effort,
            provider_options=provider_options,
        )
        chain = self.get_provider(provider)
        return await self._arouted(chain, request, with_timeout(budget, timeout), chain.asearch)

    async def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        profile: str | None = None,
        provider_options: dict[str, Any] | None = None,
        timeout: float | None = None,
        budget: Budget | None = None,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Backends that take one URL per call are fanned out concurrently. As on
        the sync router, a partly successful call returns its failures and one
        where nothing came back raises, ``profile`` is the saved identity
        whose cookies each fetch carries, and ``timeout`` keeps the pages that
        came back in time.
        """
        request = build_extract_request(urls, format=format, profile=profile, provider_options=provider_options)
        chain = self.get_extract_provider(provider)
        return await self._arouted(chain, request, with_timeout(budget, timeout), chain.aextract)

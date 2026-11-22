"""``extract()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.extract`` is where it always was; ``search`` is in
:mod:`web_router.client_search`. Beside each page ``extract`` lists the data APIs
whose catalog entries serve that page's site, found locally and never called
(see :mod:`web_router.data_api_discovery`).

``timeout=`` on ``extract``, ``search`` and ``answer`` is the shortcut for ``budget=Budget(time_limit_s=...)``:
the call waits at most that many seconds, ``auto`` starts a member only while
time is left and passes over one whose measured p90 would not fit, and an
extract keeps every page that came back in time, reporting the rest as
failures. Given beside a ``budget``, the tighter time cap holds.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from web_router.client_requests import build_extract_request, with_timeout
from web_router.data_api_discovery import load_catalog, warm_catalog, with_site_matches
from web_router.providers.base import ExtractProvider
from web_router.routing import Budget
from web_router.routing.routed import AsyncRouted, SyncRouted
from web_router.types import (
    ExtractFormat,
    ExtractResponse,
    Provider,
)

if TYPE_CHECKING:
    from web_router.vendors.credentials import CredentialSource


class _CoreLookup:
    """The lookup the methods here call, which the routers' shared base defines."""

    if TYPE_CHECKING:

        def get_extract_provider(self, name: str | Provider | ExtractProvider | None = None) -> ExtractProvider: ...

        def vendor_credential_source(self) -> CredentialSource: ...


class SyncCore(_CoreLookup, SyncRouted):
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
        data_apis: bool = True,
        configured_only: bool = False,
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

        ``data_apis`` (on by default) also lists, on each page's
        :attr:`~web_router.ExtractedDocument.data_apis`, the read endpoints of
        the data APIs that serve that page's site -- found locally, at no
        cost, and never called: run one with :meth:`call_data_api`.
        ``configured_only`` keeps the ones this router holds credentials for.
        """
        request = build_extract_request(urls, format=format, profile=profile, provider_options=provider_options)
        chain = self.get_extract_provider(provider)
        catalog = load_catalog(data_apis)
        response = self._routed(chain, request, with_timeout(budget, timeout), chain.extract)
        source = self.vendor_credential_source()
        return with_site_matches(response, source, catalog=catalog, configured_only=configured_only)


class AsyncCore(_CoreLookup, AsyncRouted):
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
        data_apis: bool = True,
        configured_only: bool = False,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Backends that take one URL per call are fanned out concurrently. As on
        the sync router, a partly successful call returns its failures and one
        where nothing came back raises, ``profile`` is the saved identity
        whose cookies each fetch carries, ``timeout`` keeps the pages that
        came back in time, and ``data_apis`` lists the data APIs that serve each page's site.
        """
        budget = with_timeout(budget, timeout)
        await asyncio.to_thread(warm_catalog, data_apis)  # the first read is file IO
        request = build_extract_request(urls, format=format, profile=profile, provider_options=provider_options)
        chain = self.get_extract_provider(provider)
        catalog = load_catalog(data_apis)
        response = await self._arouted(chain, request, budget, chain.aextract)
        source = self.vendor_credential_source()
        return with_site_matches(response, source, catalog=catalog, configured_only=configured_only)

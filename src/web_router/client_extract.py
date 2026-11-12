"""``extract()`` on the routers, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these as
mixins, so ``router.extract`` is where it always was. Beside each page it lists
the data APIs whose catalog entries serve that page's site, found locally and
never called (see :mod:`web_router.data_api_discovery`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from web_router.client_requests import build_extract_request
from web_router.data_api_discovery import with_site_matches

if TYPE_CHECKING:
    from web_router.client import AsyncWebRouter, WebRouter
    from web_router.providers.base import ExtractProvider
    from web_router.routing import Budget
    from web_router.types import ExtractFormat, ExtractResponse, Provider


class SyncExtract:
    def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        profile: str | None = None,
        provider_options: dict[str, Any] | None = None,
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

        ``data_apis`` (on by default) also lists, on each page's
        :attr:`~web_router.ExtractedDocument.data_apis`, the catalog endpoints
        of the data APIs that serve that page's site -- found locally, at no
        cost, and never called: run one with :meth:`call_vendor`.
        ``configured_only`` keeps the ones this router holds credentials for.
        """
        router = cast("WebRouter", self)
        request = build_extract_request(urls, format=format, profile=profile, provider_options=provider_options)
        chain = router.get_extract_provider(provider)
        response = router._routed(chain, request, budget, chain.extract)
        source = router.vendor_credential_source()
        return with_site_matches(response, source, data_apis=data_apis, configured_only=configured_only)


class AsyncExtract:
    async def extract(
        self,
        urls: str | list[str],
        *,
        provider: str | Provider | ExtractProvider | None = None,
        format: ExtractFormat | None = None,
        profile: str | None = None,
        provider_options: dict[str, Any] | None = None,
        budget: Budget | None = None,
        data_apis: bool = True,
        configured_only: bool = False,
    ) -> ExtractResponse:
        """Fetch the contents of one or more URLs through ``provider`` (or the router's default).

        Backends that take one URL per call are fanned out concurrently. As on
        the sync router, a partly successful call returns its failures and one
        where nothing came back raises, ``profile`` is the saved identity
        whose cookies each fetch carries, and ``data_apis`` lists the data APIs
        that serve each page's site.
        """
        router = cast("AsyncWebRouter", self)
        request = build_extract_request(urls, format=format, profile=profile, provider_options=provider_options)
        chain = router.get_extract_provider(provider)
        response = await router._arouted(chain, request, budget, chain.aextract)
        source = router.vendor_credential_source()
        return with_site_matches(response, source, data_apis=data_apis, configured_only=configured_only)

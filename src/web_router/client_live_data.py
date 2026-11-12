"""``live_data()`` on the routers, kept apart so ``client.py`` stops growing.

A live data source is a vendor's ready-made scraper for one site -- Nimble's
Extract Templates, Apify's Actors, Bright Data's scrapers, Oxylabs' sources --
run for this call: given the source's own inputs it fetches the site now and
answers with structured records. :meth:`live_data_sources` lists a vendor's
catalogue, :meth:`live_data_source` describes one entry, and
:meth:`live_data` runs it. With no ``provider``, :meth:`live_data_sources`
searches every configured backend at once, and each source it returns names
its own, so :meth:`live_data` and :meth:`live_data_source` take that source
whole: the caller never names the vendor. See :mod:`web_router.live_data_any`.

These were ``templates``, ``template`` and ``run_template``; the old names
still work, with a :class:`DeprecationWarning`, until they are removed.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these
as mixins, so the methods sit beside ``search`` and ``extract``.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from web_router.live_data_any import asearch_every_backend, backend_for, search_every_backend
from web_router.live_data_compat_router import AsyncTemplateMethods, SyncTemplateMethods
from web_router.live_data_search import asearch_live_data_sources, search_live_data_sources
from web_router.providers.base import LiveDataProvider
from web_router.registry import create_live_data_provider
from web_router.types import ExtractFormat, Provider, provider_name
from web_router.types_live_data import LiveDataRequest, LiveDataResponse, LiveDataSource, LiveDataSourcePage


class _LiveDataLookup:
    """What both routers share for live data: which backend serves it, and the request it is sent."""

    _providers_lock: threading.Lock
    _live_data_instances: dict[str, LiveDataProvider]

    def _config_for(self, key: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_live_data_provider(self, name: str | Provider | LiveDataProvider) -> LiveDataProvider:
        """Return the named live data provider, constructing and caching it on first use.

        The live data counterpart of :meth:`~web_router.WebRouter.get_provider`,
        with the same ownership rule -- but no default: a source's name belongs
        to one vendor, so the backend is named here, by the caller or by the
        :class:`~web_router.LiveDataSource` it passed.
        """
        if isinstance(name, LiveDataProvider):
            return name

        key = provider_name(name)
        with self._providers_lock:
            if key not in self._live_data_instances:
                self._live_data_instances[key] = create_live_data_provider(key, **self._config_for(key))
            return self._live_data_instances[key]

    @staticmethod
    def _live_data_request(
        source: str,
        inputs: Mapping[str, Any] | None,
        format: ExtractFormat | None,
        provider_options: dict[str, Any] | None,
    ) -> LiveDataRequest:
        return LiveDataRequest(
            source=source, inputs=dict(inputs or {}), format=format, provider_options=provider_options or {}
        )


class SyncLiveData(SyncTemplateMethods, _LiveDataLookup):
    def live_data_sources(
        self,
        *,
        provider: str | Provider | LiveDataProvider | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LiveDataSourcePage:
        """List one page of live data sources, or of those that match ``query``.

        Each source is a vendor's ready-made scraper for one site: run with its
        own inputs, it fetches the site and returns records with fixed fields.
        With no ``provider``, every live data backend this router has a key
        for is asked at once and their sources are merged, taken from each in
        turn; the page's ``provider`` is ``"auto"``, each source names its
        own, and a backend that failed is named in ``failures``. With
        ``provider``, only that vendor's catalogue. The page says where in the
        catalogue it sits and, when known, how many sources there are in all;
        ``limit`` and ``offset`` left unset leave the paging to the backend.

        ``query`` finds sources by keyword -- a site, a brand, the kind of
        page. It is searched the way the vendor allows: by the vendor's own
        search where its catalogue API has one (Apify), in the vendor's
        relevance order; otherwise the whole catalogue is read and matched
        here, every word ignoring case, on each source's name, site, kind of
        page and description, name and site matches first (Nimble, Bright
        Data, Oxylabs). The page's ``search`` says which happened.
        """
        keywords = query.strip() if query and query.strip() else None
        if provider is None:
            return search_every_backend(self, keywords, limit=limit, offset=offset)
        backend = self.get_live_data_provider(provider)
        if keywords:
            return search_live_data_sources(backend, keywords, limit=limit, offset=offset)
        return backend.live_data_sources(limit=limit, offset=offset)

    def live_data_source(
        self, name: str | LiveDataSource, *, provider: str | Provider | LiveDataProvider | None = None
    ) -> LiveDataSource:
        """Describe one source: the inputs a run takes and the records it returns, as JSON Schema.

        ``name`` is a :class:`~web_router.LiveDataSource` from
        :meth:`live_data_sources`, which names its own backend, or a bare name
        with ``provider`` -- optional when this router has one live data backend.
        """
        backend, source_name = backend_for(self, name, provider)
        return backend.live_data_source(source_name)

    def live_data(
        self,
        source: str | LiveDataSource,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | LiveDataProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> LiveDataResponse:
        """Run one of ``provider``'s live data sources and return what it extracted, fetched for this call.

        ``inputs`` are the source's own, named by its
        :attr:`~web_router.LiveDataSource.input_schema` -- a URL for some
        sources, an id, a slug or a search term for others. The answer's
        :attr:`~web_router.LiveDataResponse.records` hold one record for a
        product page and many for a search or a listing, and ``fetched_at``
        is when the backend says it fetched the page. ``format`` asks for the
        page beside the records. A run the backend could not complete raises
        rather than coming back empty, and an unknown name raises
        :class:`~web_router.LiveDataSourceNotFoundError`. One call is one set
        of inputs, answered through the backend's synchronous endpoint under
        that endpoint's own time cap. ``source`` is a
        :class:`~web_router.LiveDataSource` from :meth:`live_data_sources`,
        which names its own backend, or a bare name with ``provider`` --
        optional when this router has one live data backend.
        """
        backend, name = backend_for(self, source, provider)
        return backend.run(self._live_data_request(name, inputs, format, provider_options))


class AsyncLiveData(AsyncTemplateMethods, _LiveDataLookup):
    async def live_data_sources(
        self,
        *,
        provider: str | Provider | LiveDataProvider | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LiveDataSourcePage:
        """List one page of live data sources, or of those matching ``query``. As on the sync router."""
        keywords = query.strip() if query and query.strip() else None
        if provider is None:
            return await asearch_every_backend(self, keywords, limit=limit, offset=offset)
        backend = self.get_live_data_provider(provider)
        if keywords:
            return await asearch_live_data_sources(backend, keywords, limit=limit, offset=offset)
        return await backend.alive_data_sources(limit=limit, offset=offset)

    async def live_data_source(
        self, name: str | LiveDataSource, *, provider: str | Provider | LiveDataProvider | None = None
    ) -> LiveDataSource:
        """Describe one source: the inputs a run takes and the records it returns. As on the sync router."""
        backend, source_name = backend_for(self, name, provider)
        return await backend.alive_data_source(source_name)

    async def live_data(
        self,
        source: str | LiveDataSource,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | LiveDataProvider | None = None,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> LiveDataResponse:
        """Run one of ``provider``'s live data sources and return what it extracted. As on the sync router."""
        backend, name = backend_for(self, source, provider)
        return await backend.arun(self._live_data_request(name, inputs, format, provider_options))

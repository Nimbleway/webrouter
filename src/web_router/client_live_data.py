"""``live_data()`` on the routers, kept apart so ``client.py`` stops growing.

A live data source is a vendor's ready-made scraper for one site -- Nimble's
Extract Templates, Apify's Actors, Bright Data's scrapers, Oxylabs' sources --
run for this call: given the source's own inputs it fetches the site now and
answers with structured records. :meth:`live_data_sources` lists a vendor's
catalogue, :meth:`live_data_source` describes one entry, and
:meth:`live_data` runs it. All three need ``provider`` named, since a source's
name belongs to one vendor.

These were ``templates``, ``template`` and ``run_template``; the old names
still work, with a :class:`DeprecationWarning`, until they are removed.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` take these
as mixins, so the methods sit beside ``search`` and ``extract``.
"""

from __future__ import annotations

import threading
import warnings
from collections.abc import Mapping
from typing import Any

from web_router.constants_live_data import MSG_LIVE_DATA_DEPRECATED_NAME
from web_router.live_data_search import asearch_live_data_sources, search_live_data_sources
from web_router.providers.base import LiveDataProvider
from web_router.registry import create_live_data_provider
from web_router.types import ExtractFormat, Provider, provider_name
from web_router.types_live_data import LiveDataRequest, LiveDataResponse, LiveDataSource, LiveDataSourcePage


def _renamed(old: str, new: str) -> None:
    warnings.warn(MSG_LIVE_DATA_DEPRECATED_NAME.format(old=old, new=new), DeprecationWarning, stacklevel=3)


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
        to one vendor, so the caller always says which.
        """
        if isinstance(name, LiveDataProvider):
            return name

        key = provider_name(name)
        with self._providers_lock:
            if key not in self._live_data_instances:
                self._live_data_instances[key] = create_live_data_provider(key, **self._config_for(key))
            return self._live_data_instances[key]

    def get_template_provider(self, name: str | Provider | LiveDataProvider) -> LiveDataProvider:
        """Deprecated: :meth:`get_live_data_provider`."""
        _renamed("get_template_provider", "get_live_data_provider")
        return self.get_live_data_provider(name)

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


class SyncLiveData(_LiveDataLookup):
    def live_data_sources(
        self,
        *,
        provider: str | Provider | LiveDataProvider,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LiveDataSourcePage:
        """List one page of the live data sources ``provider`` holds, or of those that match ``query``.

        Each source is a vendor's ready-made scraper for one site: run with its
        own inputs, it fetches the site and returns records with fixed fields.
        Names, inputs and fields are the vendor's own, which is why
        ``provider`` has no default. The page says where in the catalogue it
        sits and, when the backend reports it, how many sources there are in
        all; ``limit`` and ``offset`` left unset leave the paging to the backend.

        ``query`` finds sources by keyword -- a site, a brand, the kind of
        page. It is searched the way the vendor allows: by the vendor's own
        search where its catalogue API has one (Apify), in the vendor's
        relevance order; otherwise the whole catalogue is read and matched
        here, every word ignoring case, on each source's name, site, kind of
        page and description, name and site matches first (Nimble, Bright
        Data, Oxylabs). The page's ``search`` says which happened.
        """
        backend = self.get_live_data_provider(provider)
        if query and query.strip():
            return search_live_data_sources(backend, query.strip(), limit=limit, offset=offset)
        return backend.live_data_sources(limit=limit, offset=offset)

    def live_data_source(self, name: str, *, provider: str | Provider | LiveDataProvider) -> LiveDataSource:
        """Describe one source: the inputs a run takes and the records it returns, as JSON Schema."""
        return self.get_live_data_provider(provider).live_data_source(name)

    def live_data(
        self,
        source: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | LiveDataProvider,
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
        that endpoint's own time cap.
        """
        request = self._live_data_request(source, inputs, format, provider_options)
        return self.get_live_data_provider(provider).run(request)

    def templates(self, **kwargs: Any) -> LiveDataSourcePage:
        """Deprecated: :meth:`live_data_sources`."""
        _renamed("templates", "live_data_sources")
        return self.live_data_sources(**kwargs)

    def template(self, name: str, **kwargs: Any) -> LiveDataSource:
        """Deprecated: :meth:`live_data_source`."""
        _renamed("template", "live_data_source")
        return self.live_data_source(name, **kwargs)

    def run_template(self, template: str, inputs: Mapping[str, Any] | None = None, **kwargs: Any) -> LiveDataResponse:
        """Deprecated: :meth:`live_data`."""
        _renamed("run_template", "live_data")
        return self.live_data(template, inputs, **kwargs)


class AsyncLiveData(_LiveDataLookup):
    async def live_data_sources(
        self,
        *,
        provider: str | Provider | LiveDataProvider,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LiveDataSourcePage:
        """List one page of ``provider``'s live data sources, or of those matching ``query``. As on the sync router."""
        backend = self.get_live_data_provider(provider)
        if query and query.strip():
            return await asearch_live_data_sources(backend, query.strip(), limit=limit, offset=offset)
        return await backend.alive_data_sources(limit=limit, offset=offset)

    async def live_data_source(self, name: str, *, provider: str | Provider | LiveDataProvider) -> LiveDataSource:
        """Describe one source: the inputs a run takes and the records it returns, as JSON Schema."""
        return await self.get_live_data_provider(provider).alive_data_source(name)

    async def live_data(
        self,
        source: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        provider: str | Provider | LiveDataProvider,
        format: ExtractFormat | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> LiveDataResponse:
        """Run one of ``provider``'s live data sources and return what it extracted. As on the sync router."""
        request = self._live_data_request(source, inputs, format, provider_options)
        return await self.get_live_data_provider(provider).arun(request)

    async def templates(self, **kwargs: Any) -> LiveDataSourcePage:
        """Deprecated: :meth:`live_data_sources`."""
        _renamed("templates", "live_data_sources")
        return await self.live_data_sources(**kwargs)

    async def template(self, name: str, **kwargs: Any) -> LiveDataSource:
        """Deprecated: :meth:`live_data_source`."""
        _renamed("template", "live_data_source")
        return await self.live_data_source(name, **kwargs)

    async def run_template(
        self, template: str, inputs: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> LiveDataResponse:
        """Deprecated: :meth:`live_data`."""
        _renamed("run_template", "live_data")
        return await self.live_data(template, inputs, **kwargs)

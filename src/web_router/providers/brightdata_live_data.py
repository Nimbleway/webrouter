"""Bright Data live data: its ready-made scrapers, run synchronously on one input, records returned.

A scraper is called by its dataset id or by its name, like every other vendor's source:
``amazon_products`` collects Amazon product pages by URL, and
``amazon_products_by_keyword`` discovers them by keyword -- one source per
scraper and collection method, named from Bright Data's own catalogue by
:mod:`~web_router.providers.brightdata_names`; Bright Data's own name as
written ("Amazon products") means the same. A dataset id (``gd_…``) is
accepted too, meaning that scraper's URL method. The catalogue is read once per
provider and kept, so a name costs one listing call, not one per run.

One run sends one input to the synchronous scrape endpoint, which collects for
this call and answers with the records; a discovery source adds Bright Data's
``type=discover_new`` and ``discover_by`` itself. Pre-collected marketplace
datasets are not here: they are not live.

Written from https://docs.brightdata.com/api-reference/web-scraper-api/synchronous-requests,
the scraper management API (``GET /datasets/v3/scrapers``) and the error-code
page, read on 2026-11-09. Verified live on 2026-11-10: the catalogue, a local
search, a description and a run of ``amazon_products`` on one product URL.

What the API does not say, this does not invent. A scraper's input and output
are listed as field names without types, so a description's schemas name
those fields and nothing more. Records carry no documented time of
collection, so ``fetched_at`` stays ``None``. A scrape that outlasts the
synchronous window (about a minute) answers with a snapshot id; the run then
polls the snapshot's progress and reads its records once it is ready, for up
to five minutes or until the call's deadline
(:mod:`~web_router.providers.brightdata_snapshots`). Past that it raises a
:class:`~web_router.exceptions_live_data.LiveDataJobError` naming the snapshot,
so the records can be fetched later rather than scraped and paid for again.
An input that failed comes back as a record with ``error`` and ``error_code``;
those are kept in ``extra["errors"]``, never counted as records, and a run
where every input failed raises.

``provider_options`` go to the scrape's query string -- ``limit_per_input``,
``custom_output_fields`` -- but never override the dataset or method a source names.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

from web_router.constants import (
    BRIGHTDATA_API_KEY_PREFIX,
    BRIGHTDATA_BASE_URL,
    ENV_BRIGHTDATA_API_KEY,
    PROVIDER_BRIGHTDATA,
)
from web_router.constants_live_data_vendors import (
    BRIGHTDATA_DEFAULT_METHOD,
    BRIGHTDATA_DISCOVER_TYPE,
    BRIGHTDATA_ID_PREFIX,
    BRIGHTDATA_PENDING_STATUS,
    BRIGHTDATA_SCRAPE_PATH,
    BRIGHTDATA_SCRAPERS_PATH,
    BRIGHTDATA_SNAPSHOT_POLL_SECONDS,
    BRIGHTDATA_SNAPSHOT_WAIT_SECONDS,
    MSG_BRIGHTDATA_POLL_FAILED,
    MSG_BRIGHTDATA_RENAMED_HINT,
)
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers._deadline import job_deadline
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers.brightdata_names import Scraper, plain_name, scrapers, slug
from web_router.providers.brightdata_shapes import described, listed, to_response
from web_router.providers.brightdata_snapshots import SnapshotWait, snapshot_id
from web_router.providers.oxylabs_jobs import wait_until
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSource,
    LiveDataSourcePage,
)

_METHOD_PARAMS = frozenset({"type", "discover_by"})
"""Query parameters a source sets from its own collection method, never from ``provider_options``."""


class BrightDataLiveDataProvider(HttpLiveDataProvider):
    """Runs Bright Data's ready-made scrapers as live data sources."""

    name: ClassVar[str] = PROVIDER_BRIGHTDATA
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    run_path: ClassVar[str] = BRIGHTDATA_SCRAPE_PATH
    base_url: ClassVar[str] = BRIGHTDATA_BASE_URL
    env_api_key: ClassVar[str] = ENV_BRIGHTDATA_API_KEY
    api_key_prefix: ClassVar[str] = BRIGHTDATA_API_KEY_PREFIX

    poll_seconds: ClassVar[float] = BRIGHTDATA_SNAPSHOT_POLL_SECONDS
    wait_seconds: ClassVar[float] = BRIGHTDATA_SNAPSHOT_WAIT_SECONDS

    def __init__(self, *, zone: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            zone: Accepted and unused: it is the Web Unlocker zone the extract
                provider reads, from the same ``provider_config["brightdata"]``
                entry; a scraper runs without one.
            **kwargs: As :class:`~web_router.providers._http_live_data.HttpLiveDataProvider`.
        """
        del zone
        super().__init__(**kwargs)
        self._catalogue: dict[str, Scraper] | None = None

    # -- names --------------------------------------------------------------

    def _index(self, payload: Any) -> dict[str, Scraper]:
        items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        self._catalogue = {scraper.name: scraper for scraper in scrapers(items)}
        return self._catalogue

    def _known(self) -> dict[str, Scraper]:
        return (
            self._catalogue if self._catalogue is not None else self._index(self._send("GET", BRIGHTDATA_SCRAPERS_PATH))
        )

    async def _aknown(self) -> dict[str, Scraper]:
        if self._catalogue is not None:
            return self._catalogue
        return self._index(await self._asend("GET", BRIGHTDATA_SCRAPERS_PATH))

    def _resolve(self, source: str, known: dict[str, Scraper] | None) -> Scraper:
        """The scraper a name means; a ``gd_…`` id is its URL method, and needs no catalogue."""
        if source.startswith(BRIGHTDATA_ID_PREFIX):
            return Scraper(name=source, dataset_id=source, method=BRIGHTDATA_DEFAULT_METHOD)
        # Bright Data's own name works as written too: "Amazon products" is amazon_products.
        scraper = (known or {}).get(source) or (known or {}).get(slug(source))
        if scraper is None:
            raise LiveDataSourceNotFoundError(self.name, source, hint=self._renamed_hint(slug(source), known or {}))
        return scraper

    @staticmethod
    def _renamed_hint(key: str, known: dict[str, Scraper]) -> str:
        """The names of the sources that would be called ``key`` had another scraper not taken it too."""
        close = sorted(name for name, s in known.items() if name != key and plain_name(s) == key)
        return MSG_BRIGHTDATA_RENAMED_HINT.format(names=", ".join(close[:5])) if close else ""

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        known = None if request.source.startswith(BRIGHTDATA_ID_PREFIX) else self._known()
        scraper = self._resolve(request.source, known)
        with self._named(request.source):
            response = self._client.post(self.run_path, **self._scrape(request, scraper), **self._clamped())
            if response.status_code != BRIGHTDATA_PENDING_STATUS:
                return self._to_response(request, response)
        # Past the synchronous window the scrape goes on, billed: wait for its snapshot rather than drop it.
        snapshot = self._snapshot(request, response)
        with job_deadline(snapshot.until):
            while not self._ready(snapshot):
                time.sleep(snapshot.pause())
                snapshot.check()
            with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
                return self._to_response(request, self._client.get(snapshot.records_path, **self._read()))

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        known = None if request.source.startswith(BRIGHTDATA_ID_PREFIX) else await self._aknown()
        scraper = self._resolve(request.source, known)
        with self._named(request.source):
            response = await self._async_client.post(self.run_path, **self._scrape(request, scraper), **self._clamped())
            if response.status_code != BRIGHTDATA_PENDING_STATUS:
                return self._to_response(request, response)
        snapshot = self._snapshot(request, response)
        with job_deadline(snapshot.until):
            while not await self._aready(snapshot):
                await asyncio.sleep(snapshot.pause())
                snapshot.check()
            with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
                return self._to_response(request, await self._async_client.get(snapshot.records_path, **self._read()))

    def _snapshot(self, request: LiveDataRequest, response: Any) -> SnapshotWait:
        found = snapshot_id(self.name, request, self._decoded(response))
        return SnapshotWait(self.name, request, found, wait_until(self.wait_seconds), self.poll_seconds)

    def _ready(self, snapshot: SnapshotWait) -> bool:
        with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
            return snapshot.done(self._send("GET", snapshot.path))

    async def _aready(self, snapshot: SnapshotWait) -> bool:
        with snapshot.naming(MSG_BRIGHTDATA_POLL_FAILED):
            return snapshot.done(await self._asend("GET", snapshot.path))

    def _read(self) -> dict[str, Any]:
        """A snapshot's records as JSON. The snapshot endpoint refuses ``include_errors`` (HTTP 400, checked live on
        2026-11-12); a failed input's row comes back with the records either way, and lands in ``extra["errors"]``."""
        return {"params": {"format": "json"}, **self._clamped()}

    @staticmethod
    def _scrape(request: LiveDataRequest, scraper: Scraper) -> dict[str, Any]:
        # Options first, then what the source names: an option can never pick another dataset or method,
        # nor turn a URL source into discovery, so `type` and `discover_by` come only from the source.
        chosen = {key: value for key, value in request.provider_options.items() if key not in _METHOD_PARAMS}
        params: dict[str, Any] = {**chosen, "dataset_id": scraper.dataset_id}
        if scraper.discovers:
            params.update(type=BRIGHTDATA_DISCOVER_TYPE, discover_by=scraper.discover_by)
        params.update(format="json", include_errors="true")
        return {"params": params, "json": BrightDataLiveDataProvider._build_payload(request)}

    @staticmethod
    def _build_payload(request: LiveDataRequest) -> dict[str, Any]:
        """The scrape's body: one input, as given; options go to the query string instead."""
        return {"input": [dict(request.inputs)]}

    def _to_response(self, request: LiveDataRequest, response: Any) -> LiveDataResponse:
        return to_response(self.name, request, response.status_code, self._decoded(response))

    # -- catalogue ----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(self._known(), limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(await self._aknown(), limit=limit, offset=offset)

    def _to_page(self, known: dict[str, Scraper], *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """One page of the sources. Bright Data answers with its whole catalogue, so the paging is done here."""
        return self._paged(
            list(known.values()),
            lambda scraper: listed(self.name, scraper),
            limit=limit,
            offset=offset,
            # The vendor's own entries behind this page, once each: a scraper's methods share one.
            raw=lambda page: list({scraper.dataset_id: scraper.item for scraper in page}.values()),
        )

    def _get(self, name: str) -> LiveDataSource:
        scraper = self._resolve(name, None if name.startswith(BRIGHTDATA_ID_PREFIX) else self._known())
        with self._named(name):
            payload = self._send("GET", BRIGHTDATA_SCRAPERS_PATH, params={"dataset_id": scraper.dataset_id})
        return described(self.name, scraper, payload)

    async def _aget(self, name: str) -> LiveDataSource:
        scraper = self._resolve(name, None if name.startswith(BRIGHTDATA_ID_PREFIX) else await self._aknown())
        with self._named(name):
            payload = await self._asend("GET", BRIGHTDATA_SCRAPERS_PATH, params={"dataset_id": scraper.dataset_id})
        return described(self.name, scraper, payload)

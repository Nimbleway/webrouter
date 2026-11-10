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
synchronous window (about a minute) raises with the snapshot id Bright Data
returns, so the records can be fetched later -- it is not waited for here.
An input that failed comes back as a record with ``error`` and ``error_code``;
those are kept in ``extra["errors"]``, never counted as records, and a run
where every input failed raises.

``provider_options`` go to the scrape's query string -- ``limit_per_input``,
``custom_output_fields`` -- but never override the dataset or method a source names.
"""

from __future__ import annotations

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
    MSG_BRIGHTDATA_ALL_FAILED,
    MSG_BRIGHTDATA_PENDING,
)
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers.brightdata_names import Scraper, scrapers, slug
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSample,
    LiveDataSource,
    LiveDataSourcePage,
)

_METHOD_PARAMS = frozenset({"type", "discover_by"})
"""Query parameters a source sets from its own collection method, never from ``provider_options``."""

_LISTED_MAPPED = frozenset({"id", "name", "description", "domain", "category"})


def _fields_schema(fields: Any) -> dict[str, Any] | None:
    """A JSON Schema naming the fields Bright Data lists, with no types: the API states none."""
    if isinstance(fields, dict):
        names = list(fields)
    elif isinstance(fields, list):
        names = [field if isinstance(field, str) else (field or {}).get("name") for field in fields]
    else:
        return None
    names = [name for name in names if isinstance(name, str)]
    return {"type": "object", "properties": {name: {} for name in names}} if names else None


def _samples(inputs: Any, outputs: Any) -> list[LiveDataSample]:
    """Bright Data's samples: an input object or, as its management API shows them, a list of them.

    A list of outputs pairs with the inputs by position when the lengths match;
    otherwise each sample carries its input alone rather than a guessed output.
    """
    if isinstance(inputs, dict):
        return [LiveDataSample(input=inputs, output=outputs)]
    if not isinstance(inputs, list):
        return []
    rows = [row for row in inputs if isinstance(row, dict)]
    paired = isinstance(outputs, list) and len(outputs) == len(rows)
    return [LiveDataSample(input=row, output=outputs[n] if paired else None) for n, row in enumerate(rows)]


class BrightDataLiveDataProvider(HttpLiveDataProvider):
    """Runs Bright Data's ready-made scrapers as live data sources."""

    name: ClassVar[str] = PROVIDER_BRIGHTDATA
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    run_path: ClassVar[str] = BRIGHTDATA_SCRAPE_PATH
    base_url: ClassVar[str] = BRIGHTDATA_BASE_URL
    env_api_key: ClassVar[str] = ENV_BRIGHTDATA_API_KEY
    api_key_prefix: ClassVar[str] = BRIGHTDATA_API_KEY_PREFIX

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
            raise LiveDataSourceNotFoundError(self.name, source)
        return scraper

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        known = None if request.source.startswith(BRIGHTDATA_ID_PREFIX) else self._known()
        scraper = self._resolve(request.source, known)
        with self._named(request.source):
            response = self._client.post(self.run_path, **self._scrape(request, scraper), **self._clamped())
            return self._to_response(request, response)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        known = None if request.source.startswith(BRIGHTDATA_ID_PREFIX) else await self._aknown()
        scraper = self._resolve(request.source, known)
        with self._named(request.source):
            response = await self._async_client.post(self.run_path, **self._scrape(request, scraper), **self._clamped())
            return self._to_response(request, response)

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
        payload = self._decoded(response)
        if response.status_code == BRIGHTDATA_PENDING_STATUS:
            snapshot_id = (payload or {}).get("snapshot_id")
            raise ProviderError(
                self.name, MSG_BRIGHTDATA_PENDING.format(source=request.source, snapshot_id=snapshot_id)
            )
        rows = [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        errors = [row for row in rows if row.get("error") or row.get("error_code")]
        records = [row for row in rows if row not in errors]
        if errors and not records:
            detail = "; ".join(str(row.get("error_code") or row.get("error")) for row in errors)
            raise ProviderError(self.name, MSG_BRIGHTDATA_ALL_FAILED.format(source=request.source, errors=detail))
        return LiveDataResponse(
            provider=self.name,
            source=request.source,
            inputs=dict(request.inputs),
            records=records,
            extra={"errors": errors} if errors else {},
            raw=payload,
        )

    # -- catalogue ----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(self._known(), limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(await self._aknown(), limit=limit, offset=offset)

    def _to_page(self, known: dict[str, Scraper], *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """One page of the sources. Bright Data answers with its whole catalogue, so the paging is done here."""
        every = list(known.values())
        page, start = self._local_page(every, limit=limit, offset=offset)
        return LiveDataSourcePage(
            provider=self.name,
            sources=[self._listed(scraper) for scraper in page],
            total=len(every),
            offset=start,
            limit=limit,
            # The vendor's own entries behind this page, once each: a scraper's methods share one.
            raw=list({scraper.dataset_id: scraper.item for scraper in page}.values()),
        )

    def _listed(self, scraper: Scraper) -> LiveDataSource:
        item = scraper.item
        how = "by URL" if not scraper.discovers else "discovered by " + scraper.discover_by.replace("_", " ")
        title = str(item.get("name") or scraper.dataset_id)
        return LiveDataSource(
            provider=self.name,
            name=scraper.name,
            description=" -- ".join(part for part in (f"{title}, {how}", item.get("description")) if part),
            domain=item.get("domain"),
            entity_type=item.get("category"),
            published=True,
            extra={
                "dataset_id": scraper.dataset_id,
                "collection_method": scraper.method,
                **{key: value for key, value in item.items() if key not in _LISTED_MAPPED},
            },
        )

    def _get(self, name: str) -> LiveDataSource:
        scraper = self._resolve(name, None if name.startswith(BRIGHTDATA_ID_PREFIX) else self._known())
        with self._named(name):
            payload = self._send("GET", BRIGHTDATA_SCRAPERS_PATH, params={"dataset_id": scraper.dataset_id})
        return self._described(scraper, payload)

    async def _aget(self, name: str) -> LiveDataSource:
        scraper = self._resolve(name, None if name.startswith(BRIGHTDATA_ID_PREFIX) else await self._aknown())
        with self._named(name):
            payload = await self._asend("GET", BRIGHTDATA_SCRAPERS_PATH, params={"dataset_id": scraper.dataset_id})
        return self._described(scraper, payload)

    def _described(self, scraper: Scraper, payload: Any) -> LiveDataSource:
        items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        item = next((entry for entry in items if entry.get("id") == scraper.dataset_id), None)
        if item is None:
            raise LiveDataSourceNotFoundError(self.name, scraper.name)
        found = item.get("scrapers")
        methods: dict[str, Any] = found if isinstance(found, dict) else {}
        method: dict[str, Any] = methods.get(scraper.method) or {}
        listed = self._listed(
            Scraper(name=scraper.name, dataset_id=scraper.dataset_id, method=scraper.method, item=item)
        )
        return listed.model_copy(
            update={
                "input_schema": _fields_schema(method.get("input_schema")),
                "output_schema": _fields_schema(method.get("output_fields")),
                "samples": _samples(method.get("sample_input"), method.get("sample_output")),
                "extra": {**listed.extra, "collection_methods": sorted(methods)},
                "raw": item,
            }
        )

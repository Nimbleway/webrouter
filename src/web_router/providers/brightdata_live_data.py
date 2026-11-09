"""Bright Data live data: its ready-made scrapers, run synchronously on one input, records returned.

A scraper is named by its dataset id (``gd_l7q7dkf244hwjntr0`` is Amazon
products) and takes inputs keyed as it documents -- a product URL for most,
a keyword for the discovery methods. One run sends one input to the
synchronous scrape endpoint, which collects for this call and answers with the
records. Pre-collected marketplace datasets are not here: they are not live.

Written from https://docs.brightdata.com/api-reference/web-scraper-api/synchronous-requests,
the scraper management API (``GET /datasets/v3/scrapers``) and the error-code
page, read on 2026-11-09. **Not yet verified live**: no Bright Data token was
available.

What the API does not say, this does not invent. A scraper's input and output
are listed as field names without types, so a description's schemas name
those fields and nothing more. Records carry no documented time of
collection, so ``fetched_at`` stays ``None``. A scrape that outlasts the
synchronous window (about a minute) raises with the snapshot id Bright Data
returns, so the records can be fetched later -- it is not waited for here.
An input that failed comes back as a record with ``error`` and ``error_code``;
those are kept in ``extra["errors"]``, never counted as records, and a run
where every input failed raises.

``provider_options`` go to the scrape's query string: ``type`` and
``discover_by`` for a discovery method, ``limit_per_input``,
``custom_output_fields``.
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
    BRIGHTDATA_PENDING_STATUS,
    BRIGHTDATA_SCRAPE_PATH,
    BRIGHTDATA_SCRAPERS_PATH,
    MSG_BRIGHTDATA_ALL_FAILED,
    MSG_BRIGHTDATA_PENDING,
)
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSample,
    LiveDataSource,
    LiveDataSourcePage,
)

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

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        with self._named(request.source):
            response = self._client.post(self.run_path, **self._scrape(request), **self._clamped())
            return self._to_response(request, response)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        with self._named(request.source):
            response = await self._async_client.post(self.run_path, **self._scrape(request), **self._clamped())
            return self._to_response(request, response)

    @staticmethod
    def _scrape(request: LiveDataRequest) -> dict[str, Any]:
        # Options first, then the request's own fields: an option can never name a different dataset.
        params = {**request.provider_options, "dataset_id": request.source, "format": "json", "include_errors": "true"}
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
        return self._to_page(self._send("GET", BRIGHTDATA_SCRAPERS_PATH), limit=limit, offset=offset)

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(await self._asend("GET", BRIGHTDATA_SCRAPERS_PATH), limit=limit, offset=offset)

    def _to_page(self, payload: Any, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        """One page of the catalogue. Bright Data answers with all of it, so the paging is done here."""
        scrapers = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        page, start = self._local_page(scrapers, limit=limit, offset=offset)
        return LiveDataSourcePage(
            provider=self.name,
            sources=[self._listed(item) for item in page],
            total=len(scrapers),
            offset=start,
            limit=limit,
            raw=payload,
        )

    def _listed(self, item: dict[str, Any]) -> LiveDataSource:
        return LiveDataSource(
            provider=self.name,
            name=str(item.get("id")),
            description=" -- ".join(part for part in (item.get("name"), item.get("description")) if part),
            domain=item.get("domain"),
            entity_type=item.get("category"),
            published=True,
            extra={key: value for key, value in item.items() if key not in _LISTED_MAPPED},
        )

    def _get(self, name: str) -> LiveDataSource:
        with self._named(name):
            payload = self._send("GET", BRIGHTDATA_SCRAPERS_PATH, params={"dataset_id": name})
        return self._described(name, payload)

    async def _aget(self, name: str) -> LiveDataSource:
        with self._named(name):
            payload = await self._asend("GET", BRIGHTDATA_SCRAPERS_PATH, params={"dataset_id": name})
        return self._described(name, payload)

    def _described(self, name: str, payload: Any) -> LiveDataSource:
        items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        item = next((entry for entry in items if entry.get("id") == name), None)
        if item is None:
            raise LiveDataSourceNotFoundError(self.name, name)
        found = item.get("scrapers")
        methods: dict[str, Any] = found if isinstance(found, dict) else {}
        method_name = BRIGHTDATA_DEFAULT_METHOD if BRIGHTDATA_DEFAULT_METHOD in methods else next(iter(methods), None)
        method: dict[str, Any] = (methods.get(method_name) or {}) if method_name else {}
        listed = self._listed(item)
        return listed.model_copy(
            update={
                "input_schema": _fields_schema(method.get("input_schema")),
                "output_schema": _fields_schema(method.get("output_fields")),
                "samples": _samples(method.get("sample_input"), method.get("sample_output")),
                "extra": {**listed.extra, "collection_method": method_name, "collection_methods": sorted(methods)},
                "raw": item,
            }
        )

"""Apify: live data over Store Actors, and search and extract over Apify's own Server Actors.

Live data -- :class:`ApifyLiveDataProvider` -- runs Apify Store Actors synchronously and returns their dataset
items as records; the rest of this docstring is about it. Search and extract -- :class:`ApifySearchProvider` over
``apify/web-search`` and :class:`ApifyExtractProvider` over ``apify/web-fetch`` -- are described at their classes.
All three read ``APIFY_TOKEN``.

An Actor is a ready-made scraper someone published on the Apify Store -- a
Google Maps scraper, an Amazon product scraper -- named ``username~name``
(``username/name`` is accepted too). One run takes the Actor's own input,
scrapes the site for this call, and answers with the run's dataset items:
one per product, place or post, so a search Actor returns many records.

Written from https://docs.apify.com/api/v2 and its pages for the Store
listing (``GET /v2/store``), an Actor (``GET /v2/actors/{id}``), its default
build (``GET /v2/actors/{id}/builds/default``, where the input schema lives)
and the synchronous run (``POST /v2/actors/{id}/run-sync-get-dataset-items``),
read on 2026-11-09. Verified live on 2026-11-10: a Store listing, a native
search, a description and a run of ``compass~crawler-google-places``.

What a run does not say, this does not invent: Apify documents no generic
time a page was scraped, so ``fetched_at`` stays ``None``; and an Actor
publishes no output schema, so a description's ``output_schema`` is ``None``.
The run endpoint waits at most 300 seconds; an Actor that runs longer
answers 408, raised with Apify's own message.

``provider_options`` go to the run's query string, not into the Actor's input
-- ``memory``, ``timeout``, ``maxItems``, ``maxTotalChargeUsd``, ``build`` --
because the body is the Actor's input exactly as given.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from web_router.constants_apify import (
    APIFY_ACTOR_NAME,
    APIFY_ACTOR_PATH,
    APIFY_BASE_URL,
    APIFY_DEFAULT_BUILD_PATH,
    APIFY_ITEMS_FORMAT,
    APIFY_RUN_PATH,
    APIFY_STORE_PATH,
    APIFY_WEB_FETCH_BASE_URL,
    APIFY_WEB_FETCH_DEFAULT_FORMAT,
    APIFY_WEB_FETCH_FORMATS,
    APIFY_WEB_FETCH_PATH,
    APIFY_WEB_SEARCH_BASE_URL,
    APIFY_WEB_SEARCH_MAX_DOMAINS,
    APIFY_WEB_SEARCH_PARAM_NAMES,
    APIFY_WEB_SEARCH_PARAMETERS,
    APIFY_WEB_SEARCH_PATH,
    MSG_APIFY_NO_CONTENT,
    MSG_APIFY_NO_RESULTS,
    MSG_APIFY_TOO_MANY_DOMAINS,
)
from web_router.constants_live_data import ENV_APIFY_TOKEN
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.live_data_search import NativeSourceSearch
from web_router.providers._clock import recency_start
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.providers._utils import build_payload
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    Provider,
    SearchDepth,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from web_router.types_live_data import (
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSample,
    LiveDataSource,
    LiveDataSourcePage,
)

_STORE_MAPPED = frozenset({"username", "name", "title", "description", "categories"})


def actor_id(source: str) -> str:
    """Apify's path form of an Actor name, ``username~name``, from either spelling; anything else is refused."""
    actor = source.strip().replace("/", "~")
    if not APIFY_ACTOR_NAME.fullmatch(actor):
        raise LiveDataSourceNotFoundError(Provider.APIFY.value, source)
    return actor


class ApifyLiveDataProvider(HttpLiveDataProvider, NativeSourceSearch):
    """Runs Apify Store Actors as live data sources."""

    name: ClassVar[str] = Provider.APIFY.value
    supported_parameters: ClassVar[frozenset[str]] = LIVE_DATA_PARAMETERS

    run_path: ClassVar[str] = APIFY_RUN_PATH
    base_url: ClassVar[str] = APIFY_BASE_URL
    env_api_key: ClassVar[str] = ENV_APIFY_TOKEN
    api_key_prefix: ClassVar[str] = "Bearer "

    # -- run ----------------------------------------------------------------

    def _run(self, request: LiveDataRequest) -> LiveDataResponse:
        with self._named(request.source):
            items = self._send(
                "POST", self._run_path(request), params=self._run_params(request), json=self._build_payload(request)
            )
        return self._to_response(request, items)

    async def _arun(self, request: LiveDataRequest) -> LiveDataResponse:
        with self._named(request.source):
            items = await self._asend(
                "POST", self._run_path(request), params=self._run_params(request), json=self._build_payload(request)
            )
        return self._to_response(request, items)

    @staticmethod
    def _build_payload(request: LiveDataRequest) -> dict[str, Any]:
        """The run's body: the Actor's input exactly as given; options go to the query string instead."""
        return dict(request.inputs)

    def _run_path(self, request: LiveDataRequest) -> str:
        return self.run_path.format(actor=actor_id(request.source))

    @staticmethod
    def _run_params(request: LiveDataRequest) -> dict[str, Any]:
        # The request's own fields last, so an option can never change what is run or how it answers.
        return {**request.provider_options, "format": APIFY_ITEMS_FORMAT}

    def _to_response(self, request: LiveDataRequest, items: Any) -> LiveDataResponse:
        records = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        return LiveDataResponse(
            provider=self.name, source=request.source, inputs=dict(request.inputs), records=records, raw=items
        )

    # -- catalogue ----------------------------------------------------------

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(self._send("GET", APIFY_STORE_PATH, params={"limit": limit, "offset": offset}))

    async def _alist(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        return self._to_page(await self._asend("GET", APIFY_STORE_PATH, params={"limit": limit, "offset": offset}))

    # The Store searches itself: `search` matches an Actor's title, name, description, owner and readme,
    # and its default `sortBy=relevance` orders the matches.
    def _search(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        params = {"search": query, "limit": limit, "offset": offset}
        return self._to_page(self._send("GET", APIFY_STORE_PATH, params=params))

    async def _asearch(self, query: str, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        params = {"search": query, "limit": limit, "offset": offset}
        return self._to_page(await self._asend("GET", APIFY_STORE_PATH, params=params))

    def _to_page(self, payload: dict[str, Any]) -> LiveDataSourcePage:
        data = payload.get("data") or {}
        return LiveDataSourcePage(
            provider=self.name,
            sources=[self._listed(item) for item in data.get("items") or []],
            total=data.get("total"),
            offset=data.get("offset"),
            limit=data.get("limit"),
            raw=payload,
        )

    def _listed(self, item: dict[str, Any]) -> LiveDataSource:
        return LiveDataSource(
            provider=self.name,
            name=f"{item.get('username')}~{item.get('name')}",
            description=item.get("description") or item.get("title") or "",
            entity_type=", ".join(item.get("categories") or []) or None,
            published=True,
            extra={"title": item.get("title"), **{k: v for k, v in item.items() if k not in _STORE_MAPPED}},
            raw=item,
        )

    def _get(self, name: str) -> LiveDataSource:
        with self._named(name):
            actor = self._send("GET", APIFY_ACTOR_PATH.format(actor=actor_id(name)))
            build = self._send("GET", APIFY_DEFAULT_BUILD_PATH.format(actor=actor_id(name)))
        return self._described(name, actor, build)

    async def _aget(self, name: str) -> LiveDataSource:
        with self._named(name):
            actor = await self._asend("GET", APIFY_ACTOR_PATH.format(actor=actor_id(name)))
            build = await self._asend("GET", APIFY_DEFAULT_BUILD_PATH.format(actor=actor_id(name)))
        return self._described(name, actor, build)

    def _described(self, name: str, actor: dict[str, Any], build: dict[str, Any]) -> LiveDataSource:
        data, built = actor.get("data") or {}, build.get("data") or {}
        example = (data.get("exampleRunInput") or {}).get("body")
        sample_input = _json_object(example)
        return LiveDataSource(
            provider=self.name,
            name=actor_id(name),
            description=data.get("description") or data.get("title") or "",
            input_schema=_input_schema(built),
            samples=[LiveDataSample(input=sample_input)] if sample_input else [],
            version=built.get("buildNumber") if isinstance(built.get("buildNumber"), (int, str)) else None,
            published=bool(built),
            extra={
                "title": data.get("title"),
                "default_run_options": data.get("defaultRunOptions"),
                "pricing_infos": data.get("pricingInfos"),
            },
            raw={"actor": actor, "build": build},
        )


_MAPPED_HIT_FIELDS = frozenset({"url", "title", "snippet", "date"})
_MAPPED_PAGE_FIELDS = frozenset({"url", "fetch", "metadata", *APIFY_WEB_FETCH_FORMATS.values()})


class ApifySearchProvider(HttpSearchProvider):
    """Routes searches to Apify Web Search (``apify/web-search``), Google by default.

    Called on the Actor's server interface, ``https://apify--web-search.apify.actor``, not through ``run-sync``:
    one call is one HTTP request with the token as a Bearer header, billed one search event. It returns one
    results page of organic hits and never reads the pages, so it serves ``search_depth="lite"`` alone.
    ``provider="duckduckgo"``, ``location`` and the rest are reachable through ``provider_options``.

    A result's ``description`` and ``content`` are both its snippet; ``position``,
    ``siteName``, ``displayedUrl`` and the rest of a hit stay in ``extra``, and the
    engine's own ``search`` block (``correctedQuery``, ``totalResults``) in ``raw``.
    """

    name: ClassVar[str] = Provider.APIFY.value
    supported_parameters: ClassVar[frozenset[str]] = APIFY_WEB_SEARCH_PARAMETERS
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset({"lite"})

    base_url: ClassVar[str] = APIFY_WEB_SEARCH_BASE_URL
    search_path: ClassVar[str] = APIFY_WEB_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_APIFY_TOKEN
    api_key_prefix: ClassVar[str] = "Bearer "
    http_method: ClassVar[Literal["GET", "POST"]] = "POST"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        for field in ("include_domains", "exclude_domains"):
            domains = getattr(request, field) or []
            if len(domains) > APIFY_WEB_SEARCH_MAX_DOMAINS:
                limit, count = APIFY_WEB_SEARCH_MAX_DOMAINS, len(domains)
                raise ProviderError(self.name, MSG_APIFY_TOO_MANY_DOMAINS.format(limit=limit, count=count, field=field))
        since = recency_start(request.recency)
        return build_payload(
            request,
            APIFY_WEB_SEARCH_PARAM_NAMES,
            self,
            query=request.query,
            countryCode=request.country.upper() if request.country else None,
            languageCode=request.locale,
            dateFrom=since.isoformat() if since else None,
        )

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = payload.get("results")
        if not isinstance(hits, list):
            raise ProviderError(self.name, MSG_APIFY_NO_RESULTS.format(fields=", ".join(sorted(payload)) or "none"))
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in hits if isinstance(hit, dict) and hit.get("url")],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        snippet = hit.get("snippet") or ""
        return SearchResult(
            url=hit["url"],
            title=hit.get("title") or "",
            description=snippet,
            content=snippet,
            published_date=hit.get("date") or None,
            extra={k: v for k, v in hit.items() if k not in _MAPPED_HIT_FIELDS},
        )


class ApifyExtractProvider(HttpExtractProvider):
    """Routes extracts to Apify Web Fetch (``apify/web-fetch``), one call per URL, through Apify's unblocker.

    Called on the Actor's server interface, ``https://apify--web-fetch.apify.actor``, billed one fetch event per
    page returned. A page whose site answered an error status, or that Web Fetch could not fetch at all, is an
    :class:`ExtractFailure`. ``wait`` (render in a browser) and ``headers`` are reachable
    through ``provider_options``.
    """

    name: ClassVar[str] = Provider.APIFY.value
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset(APIFY_WEB_FETCH_FORMATS)
    default_format: ClassVar[ExtractFormat] = APIFY_WEB_FETCH_DEFAULT_FORMAT

    base_url: ClassVar[str] = APIFY_WEB_FETCH_BASE_URL
    extract_path: ClassVar[str] = APIFY_WEB_FETCH_PATH
    env_api_key: ClassVar[str] = ENV_APIFY_TOKEN
    api_key_prefix: ClassVar[str] = "Bearer "

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(request, url=urls[0], formats=[APIFY_WEB_FETCH_FORMATS[self.format_of(request)]])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        body_format = self.format_of(request)
        fetch = payload.get("fetch") or {}
        metadata = payload.get("metadata") or {}
        body = payload.get(APIFY_WEB_FETCH_FORMATS[body_format])
        # Web Fetch answers an error object with a code (UNSUPPORTED_CONTENT_TYPE, a blocked fetch) instead of a
        # body; that URL fails here so auto can try the next backend for it.
        if body is None:
            error = payload.get("error") or payload.get("message") or MSG_APIFY_NO_CONTENT
            failure = ExtractFailure(url=urls[0], error=str(error), status_code=fetch.get("httpStatusCode"))
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload])
        document = ExtractedDocument(
            url=fetch.get("loadedUrl") or payload.get("url") or urls[0],
            title=metadata.get("title") or "",
            content=body,
            format=body_format,
            status_code=fetch.get("httpStatusCode"),
            extra={
                "fetch": fetch,
                "metadata": {k: v for k, v in metadata.items() if k != "title"},
                **{k: v for k, v in payload.items() if k not in _MAPPED_PAGE_FIELDS},
            },
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])


def _input_schema(build: dict[str, Any]) -> dict[str, Any] | None:
    """The build's input schema: ``actorDefinition.input`` as an object, else the deprecated ``inputSchema`` string."""
    schema = (build.get("actorDefinition") or {}).get("input")
    if isinstance(schema, dict):
        return schema
    return _json_object(build.get("inputSchema"))


def _json_object(value: Any) -> dict[str, Any] | None:
    """A JSON object from a dict or from a JSON string holding one; ``None`` for anything else."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None

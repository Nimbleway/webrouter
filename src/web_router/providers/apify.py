"""Apify live data: Apify Store Actors, run synchronously, their dataset items returned as records.

An Actor is a ready-made scraper someone published on the Apify Store -- a
Google Maps scraper, an Amazon product scraper -- named ``username~name``
(``username/name`` is accepted too). One run takes the Actor's own input,
scrapes the site for this call, and answers with the run's dataset items:
one per product, place or post, so a search Actor returns many records.

Written from https://docs.apify.com/api/v2 and its pages for the Store
listing (``GET /v2/store``), an Actor (``GET /v2/actors/{id}``), its default
build (``GET /v2/actors/{id}/builds/default``, where the input schema lives)
and the synchronous run (``POST /v2/actors/{id}/run-sync-get-dataset-items``),
read on 2026-11-09. **Not yet verified live**: no Apify token was available.

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
from typing import Any, ClassVar

from web_router.constants_apify import (
    APIFY_ACTOR_PATH,
    APIFY_BASE_URL,
    APIFY_DEFAULT_BUILD_PATH,
    APIFY_ITEMS_FORMAT,
    APIFY_RUN_PATH,
    APIFY_STORE_PATH,
)
from web_router.constants_live_data import ENV_APIFY_TOKEN
from web_router.live_data_search import NativeSourceSearch
from web_router.providers._http_live_data import HttpLiveDataProvider
from web_router.types import Provider
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
    """Apify's path form of an Actor name: ``username~name``, from either spelling."""
    return source.strip().replace("/", "~")


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


def _input_schema(build: dict[str, Any]) -> dict[str, Any] | None:
    """The build's input schema: ``actorDefinition.input`` as an object, else the deprecated ``inputSchema`` string."""
    definition = build.get("actorDefinition") or {}
    schema = definition.get("input")
    if isinstance(schema, dict):
        return schema
    return _json_object(build.get("inputSchema"))


def _json_object(value: Any) -> dict[str, Any] | None:
    """A JSON object from a dict or from a JSON string holding one; ``None`` for anything else."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None

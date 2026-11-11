"""Apify live data: Store Actors listed, described from their default build, and run synchronously.

The response shapes follow https://docs.apify.com/api/v2 (Store listing,
Actor, default build, run-sync-get-dataset-items). Names and values are
synthetic; the provider was verified live on 2026-11-10.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from web_router import WebRouter
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers.apify import ApifyLiveDataProvider, actor_id
from web_router.types_live_data import LiveDataRequest

ACTOR = "apify~google-maps-scraper"
ITEMS = [{"title": "Cafe A", "rating": 4.5}, {"title": "Cafe B", "rating": 4.1}]
SCHEMA = {"title": "Input", "type": "object", "properties": {"searchStringsArray": {"type": "array"}}}


def _provider(respond: Any) -> tuple[ApifyLiveDataProvider, Api]:
    api = Api(respond)
    return wire(ApifyLiveDataProvider(api_key="apify-token"), api), api  # type: ignore[return-value]


def test_a_run_posts_the_input_as_given_and_returns_every_item_as_a_record() -> None:
    provider, api = _provider(lambda request: httpx.Response(201, json=ITEMS))
    inputs = {"searchStringsArray": ["cafe"], "maxCrawledPlacesPerSearch": 2}
    response = provider.run(LiveDataRequest(source="apify/google-maps-scraper", inputs=inputs))

    assert api.last.method == "POST"
    assert api.last.url.path == "/v2/actors/apify~google-maps-scraper/run-sync-get-dataset-items"
    assert api.last.url.params["format"] == "json"
    assert api.body() == inputs, "the body is the Actor's input, untouched"
    assert api.last.headers["Authorization"] == "Bearer apify-token"
    assert response.records == ITEMS
    assert response.data == ITEMS[0]
    assert response.fetched_at is None, "Apify documents no scrape time"
    assert response.raw == ITEMS


def test_provider_options_go_to_the_query_not_into_the_input() -> None:
    provider, api = _provider(lambda request: httpx.Response(201, json=[]))
    provider.run(LiveDataRequest(source=ACTOR, inputs={"q": 1}, provider_options={"memory": 1024, "maxItems": 5}))

    assert api.last.url.params["memory"] == "1024"
    assert api.last.url.params["maxItems"] == "5"
    assert api.body() == {"q": 1}


@pytest.mark.parametrize(
    ("status", "error_type"), [(400, "invalid-request"), (402, "not-enough-usage"), (408, "run-timeout-exceeded")]
)
def test_a_refused_or_timed_out_run_raises_with_apifys_message(status: int, error_type: str) -> None:
    body = {"error": {"type": error_type, "message": "Actor run exceeded the timeout of 300 seconds"}}
    provider, _ = _provider(lambda request: httpx.Response(status, json=body))
    with pytest.raises(ProviderError, match=str(status)):
        provider.run(LiveDataRequest(source=ACTOR))


def test_an_unknown_actor_is_not_found() -> None:
    body = {"error": {"type": "record-not-found", "message": "Actor was not found"}}
    provider, _ = _provider(lambda request: httpx.Response(404, json=body))
    with pytest.raises(LiveDataSourceNotFoundError) as raised:
        provider.run(LiveDataRequest(source="nobody~nothing"))
    assert raised.value.source == "nobody~nothing"


def test_the_store_listing_maps_each_actor() -> None:
    store = {
        "data": {
            "total": 4200,
            "offset": 10,
            "limit": 2,
            "items": [
                {
                    "id": "abc",
                    "username": "apify",
                    "name": "google-maps-scraper",
                    "title": "Google Maps Scraper",
                    "description": "Places, reviews and contacts from Google Maps.",
                    "categories": ["TRAVEL", "LEAD_GENERATION"],
                    "stats": {"totalUsers": 100},
                }
            ],
        }
    }
    provider, api = _provider(lambda request: httpx.Response(200, json=store))
    page = provider.live_data_sources(limit=2, offset=10)

    assert api.last.url.path == "/v2/store"
    assert dict(api.last.url.params) == {"limit": "2", "offset": "10"}
    assert page.total == 4200 and page.offset == 10 and page.limit == 2
    source = page.sources[0]
    assert source.name == ACTOR
    assert source.description == "Places, reviews and contacts from Google Maps."
    assert source.entity_type == "TRAVEL, LEAD_GENERATION"
    assert source.extra["stats"] == {"totalUsers": 100}
    assert source.input_schema is None, "the Store listing carries no schema unless asked"


def test_a_query_is_searched_by_the_store_itself() -> None:
    store = {"data": {"total": 1, "offset": 0, "limit": 5, "items": [{"username": "apify", "name": "web-scraper"}]}}
    provider, api = _provider(lambda request: httpx.Response(200, json=store))
    page = WebRouter().live_data_sources(provider=provider, query="web scraper", limit=5)

    assert api.last.url.path == "/v2/store"
    assert dict(api.last.url.params) == {"search": "web scraper", "limit": "5"}
    assert page.names == ["apify~web-scraper"]
    assert page.search == "native" and page.query == "web scraper"
    assert page.total == 1, "the Store's own count of its matches"


@pytest.mark.parametrize("schema_field", ["object", "string"])
def test_a_description_reads_the_input_schema_from_the_default_build(schema_field: str) -> None:
    actor = {
        "data": {"title": "Google Maps Scraper", "description": "Places.", "exampleRunInput": {"body": '{"q": 1}'}}
    }
    build: dict[str, Any] = {"buildNumber": 7}
    if schema_field == "object":
        build["actorDefinition"] = {"input": SCHEMA}
    else:
        build["inputSchema"] = json.dumps(SCHEMA)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": build} if request.url.path.endswith("/builds/default") else actor)

    provider, api = _provider(respond)
    source = provider.live_data_source("apify/google-maps-scraper")

    assert [r.url.path for r in api.requests] == [
        "/v2/actors/apify~google-maps-scraper",
        "/v2/actors/apify~google-maps-scraper/builds/default",
    ]
    assert source.name == ACTOR
    assert source.input_schema == SCHEMA
    assert source.output_schema is None, "an Actor publishes no output schema"
    assert source.samples[0].input == {"q": 1}
    assert source.version == 7 and source.published


async def test_the_async_path() -> None:
    provider, _ = _provider(lambda request: httpx.Response(201, json=ITEMS))
    response = await provider.arun(LiveDataRequest(source=ACTOR, inputs={"q": 1}))
    await provider.aclose()
    assert response.records == ITEMS


@pytest.mark.parametrize("source", ["me~x?", "#", "a~b#c", "a b~c", "a~b~c"])
def test_a_name_that_is_not_an_actor_is_refused_before_any_request(source: str) -> None:
    provider, api = _provider(lambda request: httpx.Response(201, json=ITEMS))
    with pytest.raises(LiveDataSourceNotFoundError) as raised:
        provider.run(LiveDataRequest(source=source, inputs={"a": 1}))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source(source)
    assert raised.value.source == source
    assert api.requests == [], "nothing was sent"
    # Either spelling of a real Actor still runs.
    assert actor_id("apify/google-maps-scraper") == actor_id("apify~google-maps-scraper") == ACTOR
    assert actor_id("Mt5RqBNaZsoDF7Jvs") == "Mt5RqBNaZsoDF7Jvs"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("APIFY_TOKEN"), reason="APIFY_TOKEN not set")
def test_live_store_describe_and_run() -> None:
    with WebRouter() as router:
        page = router.live_data_sources(provider="apify", limit=3)
        assert page.sources
        source = router.live_data_source("apify~website-content-crawler", provider="apify")
        assert source.input_schema
        response = router.live_data(
            "apify~website-content-crawler",
            {"startUrls": [{"url": "https://example.com"}], "maxCrawlPages": 1},
            provider="apify",
        )
        assert response.records


def test_options_cannot_change_the_answer_format() -> None:
    provider, api = _provider(lambda request: httpx.Response(201, json=ITEMS))
    provider.run(LiveDataRequest(source=ACTOR, inputs={}, provider_options={"format": "csv"}))
    assert api.last.url.params["format"] == "json"


def test_a_string_build_number_is_the_version() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/builds/default"):
            return httpx.Response(200, json={"data": {"buildNumber": "0.1.1", "actorDefinition": {"input": SCHEMA}}})
        return httpx.Response(200, json={"data": {"title": "T"}})

    provider, _ = _provider(respond)
    assert provider.live_data_source(ACTOR).version == "0.1.1"

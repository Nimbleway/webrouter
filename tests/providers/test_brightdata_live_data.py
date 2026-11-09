"""Bright Data live data: ready-made scrapers listed, described, and run on one input synchronously.

Shapes follow https://docs.brightdata.com (synchronous requests, the scraper
management API, error codes). Values are synthetic. Not yet verified live.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from web_router import WebRouter
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers.brightdata_live_data import BrightDataLiveDataProvider
from web_router.types_live_data import LiveDataRequest

DATASET = "gd_l7q7dkf244hwjntr0"
URL = "https://www.amazon.com/dp/B000000001"
SCRAPER = {
    "id": DATASET,
    "name": "Amazon products",
    "description": "Product pages from Amazon.",
    "domain": "amazon.com",
    "category": "E-commerce",
    "scraper_type": "pdp",
    "scrapers": {
        "collect_by_url": {
            "input_schema": ["url", "zipcode"],
            "output_fields": ["title", "final_price", "rating"],
            "sample_input": {"url": URL},
            "sample_output": {"title": "A thing", "final_price": 9.99},
        },
        "discover_by_keyword": {"input_schema": ["keyword"], "output_fields": ["title"]},
    },
}


def _provider(respond: Any) -> tuple[BrightDataLiveDataProvider, Api]:
    api = Api(respond)
    return wire(BrightDataLiveDataProvider(api_key="bd-token"), api), api  # type: ignore[return-value]


def test_a_run_sends_one_input_and_returns_the_records() -> None:
    records = [{"title": "A thing", "final_price": 9.99, "url": URL}]
    provider, api = _provider(lambda request: httpx.Response(200, json=records))
    response = provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))

    assert api.last.url.path == "/datasets/v3/scrape"
    assert dict(api.last.url.params) == {"dataset_id": DATASET, "format": "json", "include_errors": "true"}
    assert api.body() == {"input": [{"url": URL}]}
    assert api.last.headers["Authorization"] == "Bearer bd-token"
    assert response.records == records and response.data == records[0]
    assert response.fetched_at is None


def test_a_discovery_method_goes_through_provider_options() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=[]))
    options = {"type": "discover_new", "discover_by": "keyword"}
    response = provider.run(LiveDataRequest(source=DATASET, inputs={"keyword": "x"}, provider_options=options))

    assert api.last.url.params["discover_by"] == "keyword"
    assert response.records == [], "Bright Data answers [] when there is no data, which is not an error"


def test_failed_inputs_are_kept_apart_from_the_records() -> None:
    rows = [{"title": "ok"}, {"input": {"url": URL}, "error": "Page not found", "error_code": "dead_page"}]
    provider, _ = _provider(lambda request: httpx.Response(200, json=rows))
    response = provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))

    assert response.records == [{"title": "ok"}]
    assert response.extra["errors"][0]["error_code"] == "dead_page"


def test_a_run_where_every_input_failed_raises() -> None:
    rows = [{"input": {"url": URL}, "error": "Page not found", "error_code": "dead_page"}]
    provider, _ = _provider(lambda request: httpx.Response(200, json=rows))
    with pytest.raises(ProviderError, match="dead_page"):
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))


def test_a_scrape_past_the_synchronous_window_names_its_snapshot() -> None:
    body = {"snapshot_id": "sd_abc", "message": "Your request is still in progress"}
    provider, _ = _provider(lambda request: httpx.Response(202, json=body))
    with pytest.raises(ProviderError, match="sd_abc"):
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))


def test_bad_input_raises_with_bright_datas_reason() -> None:
    body = {"error": "Invalid input provided", "code": "validation_error", "errors": [["url", "Required"]]}
    provider, _ = _provider(lambda request: httpx.Response(400, json=body))
    with pytest.raises(ProviderError, match="validation_error"):
        provider.run(LiveDataRequest(source=DATASET, inputs={}))


def test_an_unknown_dataset_is_not_found() -> None:
    provider, _ = _provider(lambda request: httpx.Response(404, text="dataset does not exist"))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="gd_nope", inputs={"url": URL}))


def test_the_catalogue_is_paged_here_because_bright_data_answers_with_all_of_it() -> None:
    catalogue = [dict(SCRAPER, id=f"gd_{n}") for n in range(5)]
    provider, api = _provider(lambda request: httpx.Response(200, json=catalogue))
    page = provider.live_data_sources(limit=2, offset=1)

    assert api.last.url.path == "/datasets/v3/scrapers"
    assert [s.name for s in page.sources] == ["gd_1", "gd_2"]
    assert page.total == 5 and page.offset == 1 and page.limit == 2
    assert page.sources[0].domain == "amazon.com"
    assert page.sources[0].entity_type == "E-commerce"
    assert page.sources[0].description == "Amazon products -- Product pages from Amazon."


def test_a_description_names_the_fields_bright_data_lists_and_no_types() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    source = provider.live_data_source(DATASET)

    assert api.last.url.params["dataset_id"] == DATASET
    assert source.input_schema == {"type": "object", "properties": {"url": {}, "zipcode": {}}}
    assert source.output_schema == {"type": "object", "properties": {"title": {}, "final_price": {}, "rating": {}}}
    assert source.samples[0].input == {"url": URL}
    assert source.extra["collection_method"] == "collect_by_url"
    assert source.extra["collection_methods"] == ["collect_by_url", "discover_by_keyword"]


def test_a_description_of_an_id_not_in_the_answer_is_not_found() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source("gd_nope")


async def test_the_async_path() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[{"title": "x"}]))
    response = await provider.arun(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    await provider.aclose()
    assert response.records == [{"title": "x"}]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BRIGHTDATA_API_TOKEN"), reason="BRIGHTDATA_API_TOKEN not set")
def test_live_describe_and_run() -> None:
    with WebRouter() as router:
        source = router.live_data_source(DATASET, provider="brightdata")
        assert source.input_schema
        response = router.live_data(DATASET, {"url": "https://www.amazon.com/dp/B0CHX1W1XY"}, provider="brightdata")
        assert response.records


def test_options_cannot_name_a_different_dataset() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=[{"title": "x"}]))
    provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}, provider_options={"dataset_id": "gd_other"}))
    assert api.last.url.params["dataset_id"] == DATASET


def test_a_zone_from_the_shared_provider_config_is_accepted_and_unused() -> None:
    api = Api(lambda request: httpx.Response(200, json=[{"title": "x"}]))
    provider = wire(BrightDataLiveDataProvider(api_key="bd-token", zone="web_unlocker1"), api)
    assert provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL})).records == [{"title": "x"}]
    assert "zone" not in api.last.url.params


def test_samples_published_as_a_list_are_each_a_sample() -> None:
    method = {"sample_input": [{"url": URL}, {"url": URL + "2"}], "sample_output": [{"title": "a"}, {"title": "b"}]}
    scraper = {**SCRAPER, "scrapers": {"collect_by_url": method}}
    provider, _ = _provider(lambda request: httpx.Response(200, json=[scraper]))
    source = provider.live_data_source(DATASET)
    assert [(s.input, s.output) for s in source.samples] == [
        ({"url": URL}, {"title": "a"}),
        ({"url": URL + "2"}, {"title": "b"}),
    ]


def test_the_catalogue_keeps_the_vendor_payload_and_a_description_keeps_listing_fields() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    assert provider.live_data_sources().raw == [SCRAPER]
    assert provider.live_data_source(DATASET).extra["scraper_type"] == "pdp"


@pytest.mark.parametrize(("limit", "offset"), [(-1, None), (None, -1)])
def test_a_negative_page_is_refused(limit: int | None, offset: int | None) -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    with pytest.raises(ProviderError, match="0 or more"):
        provider.live_data_sources(limit=limit, offset=offset)

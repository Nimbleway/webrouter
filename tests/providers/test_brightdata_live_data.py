"""Bright Data live data: ready-made scrapers listed, described, and run on one input synchronously.

Shapes follow https://docs.brightdata.com (synchronous requests, the scraper
management API, error codes). Values are synthetic; the provider was verified live on 2026-11-10.
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


def _by_path(records: Any, catalogue: Any = (SCRAPER,)) -> Any:
    """The catalogue for a listing, ``records`` for a scrape: a run by name reads one, then the other."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scrapers"):
            return httpx.Response(200, json=list(catalogue))
        return httpx.Response(200, json=records)

    return respond


def test_a_scraper_is_run_by_its_name() -> None:
    records = [{"title": "A thing"}]
    provider, api = _provider(_by_path(records))
    response = provider.run(LiveDataRequest(source="amazon_products", inputs={"url": URL}))

    assert api.last.url.path == "/datasets/v3/scrape"
    assert dict(api.last.url.params) == {"dataset_id": DATASET, "format": "json", "include_errors": "true"}
    assert response.source == "amazon_products" and response.records == records


@pytest.mark.parametrize("name", ["Amazon products", "amazon_products", DATASET])
def test_a_scraper_runs_by_bright_datas_name_as_written_ours_or_its_id(name: str) -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    provider.run(LiveDataRequest(source=name, inputs={"url": URL}))
    assert api.last.url.params["dataset_id"] == DATASET and "discover_by" not in api.last.url.params


def test_a_discovery_method_is_a_source_of_its_own() -> None:
    provider, api = _provider(_by_path([]))
    response = provider.run(LiveDataRequest(source="amazon_products_by_keyword", inputs={"keyword": "x"}))

    assert dict(api.last.url.params) == {
        "dataset_id": DATASET,
        "type": "discover_new",
        "discover_by": "keyword",
        "format": "json",
        "include_errors": "true",
    }
    assert response.records == [], "Bright Data answers [] when there is no data, which is not an error"


def test_the_catalogue_is_read_once_for_every_run_by_name() -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    for _ in range(3):
        provider.run(LiveDataRequest(source="amazon_products", inputs={"url": URL}))
    assert [r.url.path for r in api.requests].count("/datasets/v3/scrapers") == 1


def test_a_dataset_id_still_runs_its_url_method_without_the_catalogue() -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    assert [r.url.path for r in api.requests] == ["/datasets/v3/scrape"]


def test_an_unknown_name_is_not_found_and_nothing_is_scraped() -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="amazon_nothing", inputs={"url": URL}))
    assert "/datasets/v3/scrape" not in [r.url.path for r in api.requests]


def test_options_cannot_change_the_method_a_name_picks() -> None:
    provider, api = _provider(_by_path([]))
    options = {"type": "discover_new", "discover_by": "category_url", "limit_per_input": 5}
    provider.run(
        LiveDataRequest(source="amazon_products_by_keyword", inputs={"keyword": "x"}, provider_options=options)
    )
    assert api.last.url.params["discover_by"] == "keyword"
    assert api.last.url.params["limit_per_input"] == "5"


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
    catalogue = [SCRAPER, {"id": "gd_x", "name": "LinkedIn people profiles", "domain": "linkedin.com"}]
    provider, api = _provider(lambda request: httpx.Response(200, json=catalogue))
    page = provider.live_data_sources(limit=2, offset=1)

    assert api.last.url.path == "/datasets/v3/scrapers"
    assert page.names == ["amazon_products_by_keyword", "linkedin_people_profiles"]
    assert page.total == 3 and page.offset == 1 and page.limit == 2
    first = page.sources[0]
    assert first.domain == "amazon.com" and first.entity_type == "E-commerce"
    assert first.description == "Amazon products, discovered by keyword -- Product pages from Amazon."
    assert first.extra["dataset_id"] == DATASET and first.extra["collection_method"] == "discover_by_keyword"


def test_scrapers_that_share_a_name_are_told_apart_by_site() -> None:
    twin = {"id": "gd_fr", "name": "Amazon products", "domain": "amazon.fr"}
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER, twin]))
    assert provider.live_data_sources().names == [
        "amazon_products_amazon_com",
        "amazon_products_amazon_com_by_keyword",
        "amazon_products_amazon_fr",
    ]


def test_a_description_names_the_fields_bright_data_lists_and_no_types() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    source = provider.live_data_source("amazon_products")

    assert api.last.url.params["dataset_id"] == DATASET
    assert source.name == "amazon_products"
    assert source.input_schema == {"type": "object", "properties": {"url": {}, "zipcode": {}}}
    assert source.output_schema == {"type": "object", "properties": {"title": {}, "final_price": {}, "rating": {}}}
    assert source.samples[0].input == {"url": URL}
    assert source.extra["collection_method"] == "collect_by_url"
    assert source.extra["collection_methods"] == ["collect_by_url", "discover_by_keyword"]


def test_a_discovery_source_is_described_by_its_own_method() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    source = provider.live_data_source("amazon_products_by_keyword")
    assert source.input_schema == {"type": "object", "properties": {"keyword": {}}}
    assert source.extra["collection_method"] == "discover_by_keyword"


def test_a_description_of_an_id_not_in_the_answer_is_not_found() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source("gd_nope")


async def test_the_async_path() -> None:
    provider, _ = _provider(_by_path([{"title": "x"}]))
    response = await provider.arun(LiveDataRequest(source="amazon_products_by_keyword", inputs={"keyword": "x"}))
    source = await provider.alive_data_source("amazon_products")
    await provider.aclose()
    assert response.records == [{"title": "x"}] and source.extra["dataset_id"] == DATASET


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BRIGHTDATA_API_TOKEN"), reason="BRIGHTDATA_API_TOKEN not set")
def test_live_describe_and_run() -> None:
    with WebRouter() as router:
        source = router.live_data_source("amazon_products", provider="brightdata")
        assert source.input_schema
        url = "https://www.amazon.com/dp/B0CHX1W1XY"
        response = router.live_data("amazon_products", {"url": url}, provider="brightdata")
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


@pytest.mark.parametrize("source", ["amazon_products", DATASET])
def test_options_cannot_turn_a_url_source_into_discovery(source: str) -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    options = {"type": "discover_new", "discover_by": "keyword"}
    provider.run(LiveDataRequest(source=source, inputs={"url": URL}, provider_options=options))
    assert "type" not in api.last.url.params and "discover_by" not in api.last.url.params


def test_a_scraper_named_like_anothers_discovery_method_keeps_both() -> None:
    lookalike = {"id": "gd_Look", "name": "Amazon products by keyword", "domain": "amazon.com"}
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER, lookalike]))
    assert provider.live_data_sources().names == [
        "amazon_products",
        f"amazon_products_by_keyword_{DATASET}",
        "amazon_products_by_keyword_gd_look",
    ]

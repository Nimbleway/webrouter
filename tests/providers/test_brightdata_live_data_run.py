"""Bright Data live data: one input run synchronously, the records and failures it answers with, and live.

Shapes follow https://docs.brightdata.com (synchronous requests, the scraper
management API, error codes). Values are synthetic; the provider was verified live on 2026-11-10.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.providers._brightdata_live_data import DATASET, URL, _by_path, _provider
from tests.providers._live_data_http import Api, wire
from web_router import WebRouter
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers.brightdata_live_data import BrightDataLiveDataProvider
from web_router.types_live_data import LiveDataRequest


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


def test_bad_input_raises_with_bright_datas_reason() -> None:
    body = {"error": "Invalid input provided", "code": "validation_error", "errors": [["url", "Required"]]}
    provider, _ = _provider(lambda request: httpx.Response(400, json=body))
    with pytest.raises(ProviderError, match="validation_error"):
        provider.run(LiveDataRequest(source=DATASET, inputs={}))


def test_an_unknown_dataset_is_not_found() -> None:
    provider, _ = _provider(lambda request: httpx.Response(404, text="dataset does not exist"))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="gd_nope", inputs={"url": URL}))


def test_a_zone_from_the_shared_provider_config_is_accepted_and_unused() -> None:
    api = Api(lambda request: httpx.Response(200, json=[{"title": "x"}]))
    provider = wire(BrightDataLiveDataProvider(api_key="bd-token", zone="web_unlocker1"), api)
    assert provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL})).records == [{"title": "x"}]
    assert "zone" not in api.last.url.params


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

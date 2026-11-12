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
from web_router.exceptions_live_data import LiveDataJobError, LiveDataSourceNotFoundError
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


def _slow_scrape(*progress: str, records: object = ({"title": "late"},)) -> object:
    """A scrape past the synchronous window: 202, then each of ``progress`` in turn, then the snapshot's ``records``."""
    left = list(progress)

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "sd_abc", "message": "Your request is still in progress"})
        if path == "/datasets/v3/progress/sd_abc":
            return httpx.Response(200, json={"status": left.pop(0) if len(left) > 1 else left[0]})
        assert path == "/datasets/v3/snapshot/sd_abc"
        return httpx.Response(200, json=list(records))

    return respond


def _waiting(respond: object, *, wait: float = 5.0) -> tuple[BrightDataLiveDataProvider, Api]:
    provider, api = _provider(respond)
    provider.poll_seconds, provider.wait_seconds = 0.0, wait  # type: ignore[misc]
    return provider, api


def test_a_scrape_past_the_synchronous_window_is_waited_for_and_read_from_its_snapshot() -> None:
    provider, api = _waiting(_slow_scrape("running", "running", "ready"))
    response = provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))

    assert response.records == [{"title": "late"}]
    paths = [request.url.path for request in api.requests]
    assert paths.count("/datasets/v3/scrape") == 1 and paths.count("/datasets/v3/progress/sd_abc") == 3
    assert dict(api.last.url.params) == {"format": "json"}


def test_a_scrape_still_running_when_the_wait_ends_names_its_snapshot() -> None:
    provider, api = _waiting(_slow_scrape("running"), wait=0.0)
    with pytest.raises(LiveDataJobError, match="sd_abc") as raised:
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    assert raised.value.job_id == "sd_abc"
    assert [request.url.path for request in api.requests].count("/datasets/v3/scrape") == 1


def test_a_failed_snapshot_names_itself() -> None:
    provider, _ = _waiting(_slow_scrape("running", "failed"))
    with pytest.raises(LiveDataJobError, match="failed") as raised:
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    assert raised.value.job_id == "sd_abc"


def test_a_canceled_snapshot_ends_the_wait_at_once() -> None:
    provider, api = _waiting(_slow_scrape("canceled"))
    with pytest.raises(LiveDataJobError, match="canceled") as raised:
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    assert raised.value.job_id == "sd_abc" and "still scraping" not in str(raised.value)
    assert [request.url.path for request in api.requests].count("/datasets/v3/progress/sd_abc") == 1


def test_a_progress_read_that_fails_still_names_the_snapshot() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "sd_abc"})
        return httpx.Response(500, text="boom")

    provider, _ = _waiting(respond)
    with pytest.raises(LiveDataJobError, match="sd_abc"):
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))


def test_a_202_without_a_snapshot_id_raises() -> None:
    provider, _ = _waiting(lambda request: httpx.Response(202, json={"message": "in progress"}))
    with pytest.raises(ProviderError, match="no snapshot id"):
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))


async def test_the_async_path_waits_for_a_snapshot_too() -> None:
    provider, _ = _waiting(_slow_scrape("running", "ready"))
    response = await provider.arun(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    await provider.aclose()
    assert response.records == [{"title": "late"}]


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

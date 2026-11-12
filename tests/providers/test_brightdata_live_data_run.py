"""Bright Data live data: one input run synchronously, the records and failures it answers with, and live.

Shapes follow https://docs.brightdata.com (synchronous requests, the scraper
management API, error codes). Values are synthetic; the provider was verified live on 2026-11-10.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

from tests.providers._brightdata_live_data import DATASET, URL, _by_path, _provider
from tests.providers._live_data_http import Api, wire
from web_router import WebRouter
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataJobError, LiveDataSourceNotFoundError
from web_router.providers._deadline import call_deadline
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


@pytest.mark.parametrize(
    ("progress", "said"),
    [
        ({"status": "failed", "error_message": "Snapshot is empty"}, "Snapshot is empty"),
        ({"status": "failed"}, "no reason"),
    ],
)
def test_a_failed_snapshot_names_itself_and_bright_datas_reason(progress: dict[str, str], said: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "sd_abc"})
        return httpx.Response(200, json=progress)

    provider, _ = _waiting(respond)
    with pytest.raises(LiveDataJobError, match=said) as raised:
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


def test_a_records_read_that_fails_still_names_the_snapshot() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "sd_abc"})
        if request.url.path == "/datasets/v3/progress/sd_abc":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(500, text="boom")

    provider, _ = _waiting(respond)
    with pytest.raises(LiveDataJobError, match="sd_abc") as raised:
        provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    assert raised.value.job_id == "sd_abc"


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_ready_snapshot_whose_inputs_all_failed_raises_as_a_synchronous_run(asynchronous: bool) -> None:
    provider, _ = _waiting(_slow_scrape("ready", records=[{"error": "bad url", "error_code": "dead_page"}]))
    request = LiveDataRequest(source=DATASET, inputs={"url": URL})
    with pytest.raises(ProviderError, match=r"every input failed.*dead_page") as raised:
        await provider.arun(request) if asynchronous else provider.run(request)
    assert "may still finish" not in str(raised.value)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_call_deadline_ends_the_wait_with_the_snapshots_id(asynchronous: bool) -> None:
    """The scrape is billed: a wait cut short by the caller's deadline still says which snapshot to read later."""
    provider, api = _waiting(_slow_scrape("running"), wait=300.0)
    provider.poll_seconds = 0.05  # type: ignore[misc]
    request = LiveDataRequest(source=DATASET, inputs={"url": URL})
    started = time.monotonic()
    with call_deadline(0.8), pytest.raises(LiveDataJobError, match="still scraping") as caught:
        if asynchronous:
            await provider.arun(request)
        else:
            provider.run(request)
    assert caught.value.job_id == "sd_abc"
    assert time.monotonic() - started < 0.8
    assert [request.url.path for request in api.requests].count("/datasets/v3/scrape") == 1


def _late_202(provider: BrightDataLiveDataProvider, scrape: float) -> list[str]:
    """Wire ``provider`` sync and async: the scrape answers 202 after ``scrape`` s, each poll "running" after 0.5 s."""
    paths: list[str] = []

    def respond(request: httpx.Request) -> tuple[float, httpx.Response]:
        paths.append(request.url.path)
        if request.url.path == "/datasets/v3/scrape":
            return scrape, httpx.Response(202, json={"snapshot_id": "sd_abc"})
        return 0.5, httpx.Response(200, json={"status": "running"})

    def sync(request: httpx.Request) -> httpx.Response:
        pause, answer = respond(request)
        time.sleep(pause)
        return answer

    async def asynchronous(request: httpx.Request) -> httpx.Response:
        pause, answer = respond(request)
        await asyncio.sleep(pause)
        return answer

    provider._sync = httpx.Client(**provider._client_kwargs, transport=httpx.MockTransport(sync))
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=httpx.MockTransport(asynchronous))
    return paths


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(("scrape", "polled"), [(0.7, False), (0.2, True)], ids=["no-time-left", "time-to-poll"])
async def test_a_202_with_no_time_left_names_its_snapshot_before_polling(
    asynchronous: bool, scrape: float, polled: bool
) -> None:
    provider, _ = _waiting(None, wait=300.0)
    provider.poll_seconds = 0.05  # type: ignore[misc]
    paths = _late_202(provider, scrape)
    request = LiveDataRequest(source=DATASET, inputs={"url": URL})
    started = time.monotonic()
    with call_deadline(1.0), pytest.raises(LiveDataJobError, match="still scraping") as caught:
        if asynchronous:
            await provider.arun(request)
        else:
            provider.run(request)
    assert caught.value.job_id == "sd_abc" and time.monotonic() - started < 1.0
    assert ("/datasets/v3/progress/sd_abc" in paths) is polled


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

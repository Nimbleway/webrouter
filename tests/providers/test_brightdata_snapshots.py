"""Bright Data live data: a scrape past the synchronous window, waited for and read from its snapshot.

Shapes follow https://docs.brightdata.com (the scraper management API's
progress and snapshot endpoints). Values are synthetic.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tests.providers._brightdata_live_data import DATASET, URL, _late_202, _slow_scrape, _waiting
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataJobError
from web_router.providers._deadline import call_deadline
from web_router.types_live_data import LiveDataRequest


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


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_records_read_that_fails_still_names_the_snapshot(asynchronous: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "sd_abc"})
        if request.url.path == "/datasets/v3/progress/sd_abc":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(500, text="boom")

    provider, _ = _waiting(respond)
    request = LiveDataRequest(source=DATASET, inputs={"url": URL})
    with pytest.raises(LiveDataJobError, match="sd_abc") as raised:
        await provider.arun(request) if asynchronous else provider.run(request)
    assert raised.value.job_id == "sd_abc"


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_ready_snapshot_whose_inputs_all_failed_raises_as_a_synchronous_run(asynchronous: bool) -> None:
    provider, _ = _waiting(_slow_scrape("ready", records=[{"error": "bad url", "error_code": "dead_page"}]))
    request = LiveDataRequest(source=DATASET, inputs={"url": URL})
    with pytest.raises(ProviderError, match=r"every input failed.*dead_page") as raised:
        await provider.arun(request) if asynchronous else provider.run(request)
    assert "may still finish" not in str(raised.value)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
async def test_a_snapshot_ready_late_in_the_wait_is_read_with_the_providers_timeout(asynchronous: bool) -> None:
    given: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "sd_abc"})
        if request.url.path == "/datasets/v3/progress/sd_abc":
            time.sleep(0.2)
            return httpx.Response(200, json={"status": "ready"})
        given.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=[{"title": "late"}])

    provider, _ = _waiting(respond, wait=0.3)
    request = LiveDataRequest(source=DATASET, inputs={"url": URL})
    response = await provider.arun(request) if asynchronous else provider.run(request)
    assert response.records == [{"title": "late"}]
    assert given == [provider._client_kwargs["timeout"]]


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

"""What the Bright Data live data tests share: a scraper's entry, a provider on a recording API, and slow scrapes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from tests.providers._live_data_http import Api, wire
from web_router.providers.brightdata_live_data import BrightDataLiveDataProvider

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

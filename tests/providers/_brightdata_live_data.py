"""What the Bright Data live data tests share: one scraper's catalogue entry and a provider wired to a recording API."""

from __future__ import annotations

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

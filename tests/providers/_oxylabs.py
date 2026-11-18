"""What the Oxylabs live data tests share: the Web API's answers and a provider wired to a recording API.

Shapes follow https://developers.oxylabs.io/products/web-api (endpoint
discovery, dedicated scrapers, delivery modes), read on 2026-11-10. Values are
synthetic. Not yet verified live.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from web_router.providers.oxylabs_live_data import OxylabsLiveDataProvider

BASE = "https://webapi.oxylabs.io"

SCRAPERS = {
    "scrapers": [
        "/v1/scrape",
        "/v1/scrape/amazon",
        "/v1/scrape/amazon/product",
        "/v1/scrape/google/search",
        "/v1/scrape/media/youtube/metadata",
        "/v1/async/scrape",
        "/v1/async/scrape/amazon/product",
        "/v1/async/scrape/batch/amazon/product",
        "/v1/async/scrape/chatgpt",
        "/v1/async/scrape/batch/chatgpt",
    ]
}

OPTIONS = {
    "query": "[string] [required] [max_len: 2048]. ASIN, e.g. `B0935DN1BN`.",
    "domain": "[string] [optional] [enum: com, de, co.uk] [default: com]. Marketplace TLD.",
    "pages": "[int] [optional] [min: 1] [max: 10]. Pages to fetch.",
    "autoselect_variant": "[bool] [optional] [default: false]. Follows the default variant.",
    "output": ["[string] [optional] [enum: html, markdown, json] [default: html]. Formats to return."],
    "json": {
        "schema": "[json] [min_len: 10]. The json schema the page is parsed into.",
        "prompt": "[string] [max_len: 8192]. The prompt naming the data.",
    },
    "callback_url": "[string] [optional]. Webhook URL for the finished job.",
}

PARSED = {"asin": "B000000001", "title": "A thing", "price": 9.99}


def _scraped(fields: Any = PARSED, **metadata: Any) -> dict[str, Any]:
    return {
        "status": 200,
        "results": [
            {
                "html": None,
                "markdown": None,
                "json": fields,
                "metadata": {"page": 1, "url": "https://www.amazon.com/dp/B000000001", "status_code": 200, **metadata},
            }
        ],
        "params": {"query": "B000000001", "output": ["json"]},
        "metadata": {"timestamp": 1787431568, "request_id": "7503078851572948993"},
    }


def _answer(run: Any) -> Any:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/scrapers":
            return httpx.Response(200, json=SCRAPERS)
        if request.method == "OPTIONS":
            return httpx.Response(200, json=OPTIONS)
        return run(request)

    return respond


def _provider(run: Any = lambda request: httpx.Response(500), **kwargs: Any) -> tuple[OxylabsLiveDataProvider, Api]:
    api = Api(_answer(run))
    return wire(OxylabsLiveDataProvider(web_api_key="web-api-key", **kwargs), api), api  # type: ignore[return-value]


def _sent(api: Api, method: str) -> list[httpx.Request]:
    return [request for request in api.requests if request.method == method]


def _job(states: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> tuple[OxylabsLiveDataProvider, Api]:
    monkeypatch.setattr(OxylabsLiveDataProvider, "poll_seconds", 0.0)
    answers = iter(states)
    return _provider(lambda request: httpx.Response(202 if request.method == "POST" else 200, json=next(answers)))


PENDING = {"status": "pending", "metadata": {"request_id": "7497734325668954113"}}

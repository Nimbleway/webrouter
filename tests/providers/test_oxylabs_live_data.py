"""Oxylabs live data: dedicated sources run through Realtime with parsing.

Shapes follow https://developers.oxylabs.io (Realtime integration, dedicated
parsers, response codes). Values are synthetic; the provider was verified live on 2026-11-10.
"""

from __future__ import annotations

import os
from base64 import b64decode
from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from web_router import WebRouter
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers.oxylabs_live_data import OxylabsLiveDataProvider
from web_router.types_live_data import LiveDataRequest

PARSED = {"asin": "B000000001", "title": "A thing", "price": 9.99, "parse_status_code": 12000}


def _result(content: Any = PARSED, **extra: Any) -> dict[str, Any]:
    return {
        "content": content,
        "created_at": "2026-11-09 10:00:00",
        "updated_at": "2026-11-09 10:00:03",
        "page": 1,
        "url": "https://www.amazon.com/dp/B000000001",
        "job_id": "7000000000000000001",
        "status_code": 200,
        "parser_type": "amazon_product",
        **extra,
    }


def _provider(respond: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[OxylabsLiveDataProvider, Api]:
    monkeypatch.setenv("OXYLABS_USERNAME", "user")
    api = Api(respond)
    return wire(OxylabsLiveDataProvider(api_key="pass"), api), api  # type: ignore[return-value]


def test_a_run_sends_the_source_its_inputs_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json={"results": [_result()]}), monkeypatch)
    inputs = {"query": "B000000001", "domain": "com", "geo_location": "90210"}
    response = provider.run(LiveDataRequest(source="amazon_product", inputs=inputs))

    assert str(api.last.url) == "https://realtime.oxylabs.io/v1/queries"
    assert api.body() == {**inputs, "source": "amazon_product", "parse": True}
    assert b64decode(api.last.headers["Authorization"].removeprefix("Basic ")) == b"user:pass"
    assert response.records == [PARSED]
    assert response.url == "https://www.amazon.com/dp/B000000001" and response.status_code == 200
    assert response.fetched_at is None, "created_at is not documented as the fetch time"
    assert response.extra["results"][0]["created_at"] == "2026-11-09 10:00:00"


def test_every_result_page_is_a_record(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {"results": [_result({**PARSED, "page": 1}), _result({**PARSED, "page": 2}, page=2)]}
    provider, _ = _provider(lambda request: httpx.Response(200, json=pages), monkeypatch)
    response = provider.run(LiveDataRequest(source="amazon_search", inputs={"query": "x", "pages": 2}))
    assert len(response.records) == 2


@pytest.mark.parametrize("code", [12004, 12005])
def test_a_partial_parse_is_still_a_record(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    payload = {"results": [_result({**PARSED, "parse_status_code": code})]}
    provider, _ = _provider(lambda request: httpx.Response(200, json=payload), monkeypatch)
    response = provider.run(LiveDataRequest(source="amazon_product", inputs={"query": "x"}))
    assert response.records[0]["parse_status_code"] == code


@pytest.mark.parametrize("code", [12002, 12009])
def test_a_failed_parse_raises(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    payload = {"results": [_result({"parse_status_code": code})]}
    provider, _ = _provider(lambda request: httpx.Response(200, json=payload), monkeypatch)
    with pytest.raises(ProviderError, match=str(code)):
        provider.run(LiveDataRequest(source="amazon_product", inputs={"query": "x"}))


def test_an_unparsed_html_body_is_not_a_record(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"results": [_result("<html>...</html>")]}
    provider, _ = _provider(lambda request: httpx.Response(200, json=payload), monkeypatch)
    with pytest.raises(ProviderError, match="could not parse"):
        provider.run(LiveDataRequest(source="amazon_product", inputs={"query": "x"}))


def test_a_page_the_site_refused_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"results": [_result(PARSED, status_code=404)]}
    provider, _ = _provider(lambda request: httpx.Response(200, json=payload), monkeypatch)
    with pytest.raises(ProviderError, match="status 404"):
        provider.run(LiveDataRequest(source="amazon_product", inputs={"query": "x"}))


def test_the_catalogue_is_the_documented_parser_list(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, api = _provider(lambda request: httpx.Response(500), monkeypatch)
    page = provider.live_data_sources(limit=3, offset=2)

    assert api.requests == [], "listing calls nothing: Oxylabs has no listing endpoint"
    assert [s.name for s in page.sources] == ["amazon_product", "amazon_pricing", "amazon_bestsellers"]
    assert page.total == 31, "the dedicated parsers Oxylabs' docs list on 2026-11-09"
    assert provider.live_data_source("amazon_product").input_schema is None
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source("nowhere_product")


async def test_the_async_path(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json={"results": [_result()]}), monkeypatch)
    response = await provider.arun(LiveDataRequest(source="amazon_product", inputs={"query": "x"}))
    await provider.aclose()
    assert response.records == [PARSED]


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("OXYLABS_USERNAME") and os.getenv("OXYLABS_PASSWORD")), reason="Oxylabs credentials not set"
)
def test_live_amazon_product() -> None:
    with WebRouter() as router:
        response = router.live_data("amazon_product", {"query": "B07FZ8S74R"}, provider="oxylabs")
        assert response.records and response.records[0].get("title")


def test_options_cannot_change_the_source_or_turn_parsing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json={"results": [_result()]}), monkeypatch)
    options = {"source": "google_search", "parse": False, "render": "html"}
    provider.run(LiveDataRequest(source="amazon_product", inputs={"query": "x"}, provider_options=options))
    assert api.body()["source"] == "amazon_product" and api.body()["parse"] is True
    assert api.body()["render"] == "html"


def test_a_negative_page_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = _provider(lambda request: httpx.Response(500), monkeypatch)
    with pytest.raises(ProviderError, match="0 or more"):
        provider.live_data_sources(offset=-1)

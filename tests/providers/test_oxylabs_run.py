"""Oxylabs live data: a realtime run, its payload, and the answers that raise.

Shapes follow https://developers.oxylabs.io/products/web-api (endpoint
discovery, dedicated scrapers, delivery modes), read on 2026-11-10. Values are
synthetic. Not yet verified live.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.providers._oxylabs import BASE, PARSED, _provider, _scraped, _sent
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.types_live_data import LiveDataRequest

# -- run ----------------------------------------------------------------------


def test_a_run_posts_the_inputs_to_the_scraper_and_asks_for_its_fields() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=_scraped()))
    inputs = {"query": "B000000001", "domain": "com"}
    response = provider.run(LiveDataRequest(source="amazon/product", inputs=inputs))

    assert str(api.last.url) == f"{BASE}/v1/scrape/amazon/product" and api.last.method == "POST"
    assert api.body() == {**inputs, "output": ["json"]}
    assert response.records == [PARSED]
    assert response.url == "https://www.amazon.com/dp/B000000001" and response.status_code == 200
    assert response.request_id == "7503078851572948993"
    assert response.fetched_at is None, "metadata.timestamp is not documented as the fetch time"
    assert response.extra["metadata"]["timestamp"] == 1787431568


def test_options_cannot_ask_for_html_or_send_the_result_elsewhere() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=_scraped()))
    options = {"output": ["html"], "callback_url": "https://example.com/hook", "locale": "en_US"}
    provider.run(LiveDataRequest(source="amazon/product", inputs={"query": "x"}, provider_options=options))

    assert api.body() == {"query": "x", "locale": "en_US", "output": ["json"]}


def test_a_page_the_site_refused_raises() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=_scraped(status_code=404)))
    with pytest.raises(ProviderError, match="status 404"):
        provider.run(LiveDataRequest(source="amazon/product", inputs={"query": "x"}))


@pytest.mark.parametrize(
    "payload",
    [
        {"status": 200, "results": [], "metadata": {}},
        _scraped({}),
        _scraped(["not", "objects"]),
        _scraped([{}]),
    ],
    ids=["no results", "empty object", "no objects", "only empty objects"],
)
def test_a_run_with_no_parsed_fields_raises(payload: dict[str, Any]) -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError, match="no parsed fields"):
        provider.run(LiveDataRequest(source="amazon/product", inputs={"query": "x"}))


def test_a_list_of_records_is_every_nonempty_object() -> None:
    rows = [{"pos": 1}, {}, {"pos": 2}]
    provider, _ = _provider(lambda request: httpx.Response(200, json=_scraped(rows)))
    response = provider.run(LiveDataRequest(source="google/search", inputs={"query": "x"}))
    assert response.records == [{"pos": 1}, {"pos": 2}]


def test_an_unknown_source_is_not_found_and_nothing_is_billed() -> None:
    provider, api = _provider()
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="no/such/scraper", inputs={"query": "x"}))
    assert _sent(api, "POST") == []


def test_a_scraper_gone_since_the_listing_is_not_found() -> None:
    provider, _ = _provider(lambda request: httpx.Response(404))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="google/search", inputs={"query": "x"}))


def test_rejected_inputs_carry_oxylabs_own_words() -> None:
    problem = {"status": 400, "title": "VALIDATION_ERROR", "errors": [{"pointer": "#/query", "detail": "required"}]}
    provider, _ = _provider(lambda request: httpx.Response(400, json=problem))
    with pytest.raises(ProviderError, match="VALIDATION_ERROR"):
        provider.run(LiveDataRequest(source="amazon/product", inputs={}))

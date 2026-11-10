"""Oxylabs live data: the Web API's dedicated scrapers, listed, described with OPTIONS, and run.

Shapes follow https://developers.oxylabs.io/products/web-api (endpoint
discovery, dedicated scrapers, delivery modes), read on 2026-11-10. Values are
synthetic. Not yet verified live.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from web_router import WebRouter
from web_router.exceptions import LiveDataSourceNotFoundError, ProviderError
from web_router.providers.oxylabs_live_data import OxylabsLiveDataProvider
from web_router.types_live_data import LiveDataRequest

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
    return wire(OxylabsLiveDataProvider(api_key="web-api-key", **kwargs), api), api  # type: ignore[return-value]


def _sent(api: Api, method: str) -> list[httpx.Request]:
    return [request for request in api.requests if request.method == method]


# -- catalogue ----------------------------------------------------------------


def test_the_catalogue_is_the_web_apis_own_scraper_list() -> None:
    provider, api = _provider()
    page = provider.live_data_sources()

    assert str(api.last.url) == f"{BASE}/v1/scrapers"
    assert api.last.headers["Authorization"] == "Bearer web-api-key"
    # The generic /v1/scrape is a page fetch, not a source; a batch-only listing adds no source of its own.
    assert page.names == ["amazon", "amazon/product", "google/search", "media/youtube/metadata", "chatgpt"]
    assert page.total == 5


def test_a_listed_source_says_its_site_kind_and_modes() -> None:
    provider, _ = _provider()
    by_name = {source.name: source for source in provider.live_data_sources().sources}

    product = by_name["amazon/product"]
    assert (product.domain, product.entity_type) == ("amazon", "product")
    assert product.extra == {"endpoint": "/v1/scrape/amazon/product", "modes": ["async", "batch", "sync"]}
    assert (by_name["amazon"].domain, by_name["amazon"].entity_type) == ("amazon", None)
    assert (by_name["media/youtube/metadata"].domain, by_name["media/youtube/metadata"].entity_type) == (
        "youtube",
        "metadata",
    )
    assert by_name["chatgpt"].extra["endpoint"] == "/v1/async/scrape/chatgpt"
    assert "run as a job" in by_name["chatgpt"].description


def test_the_scraper_list_is_read_once_per_provider() -> None:
    provider, api = _provider()
    provider.live_data_sources(limit=2)
    provider.live_data_sources(limit=2, offset=2)
    provider.live_data_source("amazon/product")

    assert len([r for r in _sent(api, "GET") if r.url.path == "/v1/scrapers"]) == 1


def test_a_negative_page_is_refused() -> None:
    provider, _ = _provider()
    with pytest.raises(ProviderError, match="0 or more"):
        provider.live_data_sources(offset=-1)


# -- describe -----------------------------------------------------------------


def test_a_description_is_the_endpoints_own_parameters_as_a_schema() -> None:
    provider, api = _provider()
    source = provider.live_data_source("amazon/product")

    assert str(api.last.url) == f"{BASE}/v1/scrape/amazon/product" and api.last.method == "OPTIONS"
    schema = source.input_schema
    assert schema is not None and schema["required"] == ["query"]
    properties = schema["properties"]
    assert properties["query"] == {"type": "string", "maxLength": 2048, "description": "ASIN, e.g. `B0935DN1BN`."}
    assert properties["domain"] == {
        "type": "string",
        "enum": ["com", "de", "co.uk"],
        "default": "com",
        "description": "Marketplace TLD.",
    }
    assert properties["pages"] == {"type": "integer", "minimum": 1, "maximum": 10, "description": "Pages to fetch."}
    assert properties["autoselect_variant"]["default"] is False
    assert properties["json"]["type"] == "object"
    assert properties["json"]["properties"]["schema"] == {
        "type": "object",
        "minLength": 10,
        "description": "The json schema the page is parsed into.",
    }
    assert "output" not in properties and "callback_url" not in properties, "set by the run, not by the caller"
    assert source.raw == OPTIONS


def test_an_array_parameter_is_read_from_its_one_element_list() -> None:
    from web_router.providers.oxylabs_options import parameter

    schema, required = parameter(OPTIONS["output"])
    assert schema == {
        "type": "array",
        "items": {"type": "string", "enum": ["html", "markdown", "json"]},
        "default": ["html"],
        "description": "Formats to return.",
    }
    assert required is False


def test_an_async_only_scraper_is_described_on_its_async_path() -> None:
    provider, api = _provider()
    provider.live_data_source("chatgpt")
    assert str(api.last.url) == f"{BASE}/v1/async/scrape/chatgpt"


def test_a_source_may_be_named_by_its_path() -> None:
    provider, _ = _provider()
    assert provider.live_data_source("/v1/scrape/amazon/product").name == "amazon/product"


def test_an_unknown_source_is_not_found_without_asking_its_endpoint() -> None:
    provider, api = _provider()
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source("amazon_product")
    assert _sent(api, "OPTIONS") == []


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


def test_a_result_with_no_fields_raises() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=_scraped(None)))
    with pytest.raises(ProviderError, match="no parsed fields"):
        provider.run(LiveDataRequest(source="amazon/product", inputs={"query": "x"}))


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


# -- async-only scrapers ------------------------------------------------------


def _job(states: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> tuple[OxylabsLiveDataProvider, Api]:
    monkeypatch.setattr(OxylabsLiveDataProvider, "poll_seconds", 0.0)
    answers = iter(states)
    return _provider(lambda request: httpx.Response(202 if request.method == "POST" else 200, json=next(answers)))


PENDING = {"status": "pending", "metadata": {"request_id": "7497734325668954113"}}


def test_an_async_only_scraper_is_submitted_and_polled_until_done(monkeypatch: pytest.MonkeyPatch) -> None:
    done = {**_scraped({"response_text": "Hi"}), "status": "done"}
    provider, api = _job([PENDING, PENDING, done], monkeypatch)
    response = provider.run(LiveDataRequest(source="chatgpt", inputs={"query": "hello"}))

    posts, gets = _sent(api, "POST"), [r for r in _sent(api, "GET") if r.url.path != "/v1/scrapers"]
    assert [str(r.url) for r in posts] == [f"{BASE}/v1/async/scrape/chatgpt"]
    assert [str(r.url) for r in gets] == [f"{BASE}/v1/async/scrape/7497734325668954113"] * 2
    assert response.records == [{"response_text": "Hi"}]


async def test_an_async_only_scraper_is_polled_on_the_async_path_too(monkeypatch: pytest.MonkeyPatch) -> None:
    done = {**_scraped({"response_text": "Hi"}), "status": "done"}
    provider, _ = _job([PENDING, done], monkeypatch)
    response = await provider.arun(LiveDataRequest(source="chatgpt", inputs={"query": "hello"}))
    await provider.aclose()
    assert response.records == [{"response_text": "Hi"}]


def test_a_faulted_job_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    faulted = {"status": "faulted", "results": [{"metadata": {"status_code": 613}}], "metadata": PENDING["metadata"]}
    provider, _ = _job([PENDING, faulted], monkeypatch)
    with pytest.raises(ProviderError, match="faulted: status 613"):
        provider.run(LiveDataRequest(source="chatgpt", inputs={"query": "hello"}))


def test_a_job_still_running_past_the_wait_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OxylabsLiveDataProvider, "wait_seconds", 0.0)
    provider, _ = _job([PENDING], monkeypatch)
    with pytest.raises(ProviderError, match="GET /v1/async/scrape/7497734325668954113"):
        provider.run(LiveDataRequest(source="chatgpt", inputs={"query": "hello"}))


# -- keys ---------------------------------------------------------------------


def test_the_key_is_the_web_api_key_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OXYLABS_WEB_API_KEY", "from-env")
    api = Api(_answer(lambda request: httpx.Response(500)))
    provider = wire(OxylabsLiveDataProvider(), api)
    provider.live_data_sources()
    assert api.last.headers["Authorization"] == "Bearer from-env"


def test_a_shared_extract_config_does_not_send_the_realtime_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """``provider_config["oxylabs"]`` is extract's too: its api_key is the Realtime password, refused by the Web API."""
    monkeypatch.setenv("OXYLABS_WEB_API_KEY", "web-key")
    api = Api(_answer(lambda request: httpx.Response(500)))
    provider = wire(OxylabsLiveDataProvider(username="user", api_key="realtime-password"), api)
    provider.live_data_sources()
    assert api.last.headers["Authorization"] == "Bearer web-key"

    api = Api(_answer(lambda request: httpx.Response(500)))
    provider = wire(OxylabsLiveDataProvider(username="user", api_key="pw", web_api_key="explicit"), api)
    provider.live_data_sources()
    assert api.last.headers["Authorization"] == "Bearer explicit"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("OXYLABS_WEB_API_KEY"), reason="Oxylabs Web API key not set")
def test_live_amazon_product() -> None:
    with WebRouter() as router:
        described = router.live_data_source("amazon/product", provider="oxylabs")
        assert described.input_schema and "query" in described.input_schema["properties"]
        response = router.live_data("amazon/product", {"query": "B07FZ8S74R"}, provider="oxylabs")
        assert response.records and response.records[0].get("title")

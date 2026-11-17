"""Oxylabs live data over the Web Scraper API ("Classic"): the payload, the answers, the catalogue, and the dispatch.

Shapes follow https://developers.oxylabs.io/products/web-scraper-api/targets,
read on 2026-11-17, and the answers seen live that day: a parsed page under
``results[].content`` with ``parse_status_code`` 12000, and 12003 with no
fields for a source Oxylabs does not parse. Values here are synthetic.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from tests.providers._oxylabs import _answer, _scraped
from web_router import WebRouter
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers.oxylabs_classic import OxylabsClassicLiveDataProvider, classic_sources
from web_router.providers.oxylabs_live_data_any import OxylabsLiveDataProvider
from web_router.registry import configured_live_data_providers
from web_router.types_live_data import LiveDataRequest

BASE = "https://realtime.oxylabs.io"
PAGE = {"title": "A thing", "price": 9.99, "parse_status_code": 12000}
ENV = ("OXYLABS_USERNAME", "OXYLABS_PASSWORD", "OXYLABS_WEB_API_KEY")


def _parsed(*contents: Any, status: int = 200) -> dict[str, Any]:
    results = [
        {"content": content, "status_code": status, "url": "https://www.example.com/p/1", "job_id": "1", "page": 1}
        for content in contents or (PAGE,)
    ]
    return {"results": results, "job": {"id": "7398271642069149697", "status": "done", "source": "amazon_search"}}


def _classic(answer: dict[str, Any] | None = None, status: int = 200) -> tuple[OxylabsClassicLiveDataProvider, Api]:
    api = Api(lambda request: httpx.Response(status, json=answer if answer is not None else _parsed()))
    provider = OxylabsClassicLiveDataProvider(username="user", api_key="password")
    return wire(provider, api), api  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _no_oxylabs_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("integration"):
        return  # a live test runs on the credentials the environment holds
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


# -- run ----------------------------------------------------------------------


def test_a_run_posts_the_source_and_inputs_with_parse_and_signs_in_with_basic_auth() -> None:
    provider, api = _classic()
    response = provider.run(
        LiveDataRequest(source="amazon_search", inputs={"query": "iphone", "geo_location": "90210"})
    )

    assert str(api.last.url) == f"{BASE}/v1/queries" and api.last.method == "POST"
    assert api.last.headers["Authorization"] == "Basic dXNlcjpwYXNzd29yZA=="  # user:password
    assert api.body() == {"source": "amazon_search", "query": "iphone", "geo_location": "90210", "parse": True}
    assert response.records == [{"title": "A thing", "price": 9.99}], "parse_status_code is the API's, not a field"
    assert response.request_id == "7398271642069149697"
    assert response.url == "https://www.example.com/p/1" and response.status_code == 200
    assert response.fetched_at is None and "content" not in response.extra["results"][0]


def test_context_inputs_travel_in_the_context_array_and_transport_fields_are_never_sent() -> None:
    provider, api = _classic()
    inputs = {"query": "dQw4w9WgXcQ", "language_code": "en", "callback_url": "https://example.com/hook"}
    provider.run(LiveDataRequest(source="youtube_subtitles", inputs=inputs, provider_options={"parse": False}))

    assert api.body() == {
        "source": "youtube_subtitles",
        "query": "dQw4w9WgXcQ",
        "context": [{"key": "language_code", "value": "en"}],
    }, "a caller's parse is never sent, and this source refuses it"


def test_a_page_parsed_in_part_is_records_with_the_parsers_errors_beside_them() -> None:
    """``parse_status_code`` 12004, seen live on ``target_search``: most fields, and ``_errors`` naming the rest."""
    partial = {"results": {"organic": [{"price": 129.99}]}, "_errors": ["no title"], "parse_status_code": 12004}
    provider, _ = _classic(_parsed(partial))
    response = provider.run(LiveDataRequest(source="target_search", inputs={"query": "headphones"}))
    assert response.records == [{"results": {"organic": [{"price": 129.99}]}}]
    assert response.extra["results"][0]["parse_status_code"] == 12004
    assert response.extra["results"][0]["_errors"] == ["no title"]


def test_a_page_parsed_with_fields_missing_is_records_with_the_parsers_warnings_beside_them() -> None:
    """``parse_status_code`` 12005, seen live on ``etsy_product``: the fields it read, ``_warnings`` the rest."""
    missing = {
        "title": "A mug",
        "price": 31.72,
        "_warnings": ["Could not parse field .images"],
        "parse_status_code": 12005,
    }
    provider, _ = _classic(_parsed(missing))
    response = provider.run(LiveDataRequest(source="etsy_product", inputs={"product_id": "1"}))
    assert response.records == [{"title": "A mug", "price": 31.72}]
    assert response.extra["results"][0]["_warnings"] == ["Could not parse field .images"]


def test_a_source_answered_in_its_own_json_is_not_sent_parse_and_its_string_is_read() -> None:
    """``google_trends_explore`` refuses ``parse`` and sends its JSON as a string, as seen live."""
    provider, api = _classic(_parsed('{"interest_over_time": [{"keyword": "x"}]}'))
    response = provider.run(LiveDataRequest(source="google_trends_explore", inputs={"query": "x"}))
    assert "parse" not in api.body()
    assert response.records == [{"interest_over_time": [{"keyword": "x"}]}]


def test_a_source_that_requires_render_is_sent_it() -> None:
    provider, api = _classic()
    provider.run(LiveDataRequest(source="google_ai_mode", inputs={"query": "best laptop"}))
    assert api.body()["render"] == "html"


@pytest.mark.parametrize(
    ("answer", "status", "match"),
    [
        (_parsed({"parse_status_code": 12003}), 200, r"did not parse 'amazon_search': parse_status_code 12003"),
        (_parsed("<html></html>"), 200, "returned no parsed fields"),
        (_parsed(PAGE, status=404), 200, "could not fetch 'amazon_search': status 404"),
        ({"results": []}, 200, "returned no parsed fields"),
        ({"message": "Unsupported source."}, 400, "Unsupported source"),
    ],
)
def test_a_run_that_brings_back_no_parsed_page_raises(answer: dict[str, Any], status: int, match: str) -> None:
    provider, _ = _classic(answer, status)
    with pytest.raises(ProviderError, match=match):
        provider.run(LiveDataRequest(source="amazon_search", inputs={"query": "x"}))


async def test_an_async_run_reads_the_same_answer() -> None:
    provider, _ = _classic()
    response = await provider.arun(LiveDataRequest(source="amazon_search", inputs={"query": "x"}))
    assert response.records == [{"title": "A thing", "price": 9.99}]


# -- catalogue ----------------------------------------------------------------


def test_the_catalogue_is_the_shipped_list_with_each_sources_inputs() -> None:
    provider, api = _classic()
    page = provider.live_data_sources(limit=2, offset=1)
    names = list(classic_sources())
    assert [source.name for source in page.sources] == names[1:3] and page.total == len(names)
    assert api.requests == [], "the API lists no sources, so the catalogue is read from the package"

    described = provider.live_data_source("amazon_product")
    assert described.domain == "amazon" and described.entity_type == "product" and described.published
    assert described.input_schema is not None and described.input_schema["required"] == ["query"]
    assert "geo_location" in described.input_schema["properties"]


def test_every_shipped_source_is_parsed_and_takes_a_required_input() -> None:
    for name, entry in classic_sources().items():
        assert entry["inputs"], name
        assert any(spec["required"] for spec in entry["inputs"].values()), name
        assert entry["docs"].startswith("https://developers.oxylabs.io/"), name
    assert "universal" not in classic_sources(), "a page fetch by URL is extract's"


def test_an_unknown_source_is_not_found_before_anything_is_sent() -> None:
    provider, api = _classic()
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="shopee_search", inputs={"query": "x"}))
    assert api.requests == []


# -- one `oxylabs` over both APIs --------------------------------------------


def _both(monkeypatch: pytest.MonkeyPatch) -> tuple[OxylabsLiveDataProvider, Api, Api]:
    classic_api = Api(lambda request: httpx.Response(200, json=_parsed()))
    web_api = Api(_answer(lambda request: httpx.Response(200, json=_scraped())))
    provider = OxylabsLiveDataProvider(username="user", api_key="password", web_api_key="web-key")
    assert provider.classic is not None and provider.web_api is not None
    wire(provider.classic, classic_api)
    wire(provider.web_api, web_api)
    return provider, classic_api, web_api


def test_with_both_credentials_a_source_goes_to_the_api_that_has_it(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, classic_api, web_api = _both(monkeypatch)
    provider.run(LiveDataRequest(source="amazon_product", inputs={"query": "B000000001"}))
    provider.run(LiveDataRequest(source="amazon/product", inputs={"query": "B000000001"}))

    assert [request.url.path for request in classic_api.requests] == ["/v1/queries"]
    assert [request.url.path for request in web_api.requests if request.method == "POST"] == [
        "/v1/scrape/amazon/product"
    ]


def test_with_both_credentials_the_catalogue_is_both_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _, _ = _both(monkeypatch)
    page = provider.live_data_sources()
    names = [source.name for source in page.sources]
    assert names[: len(classic_sources())] == list(classic_sources())
    assert "amazon/product" in names and page.total == len(names)
    assert provider.live_data_sources(limit=1, offset=len(classic_sources())).sources[0].name not in classic_sources()


def test_with_the_username_and_password_alone_oxylabs_is_a_live_data_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "oxylabs" not in configured_live_data_providers()
    monkeypatch.setenv("OXYLABS_USERNAME", "user")
    assert "oxylabs" not in configured_live_data_providers(), "half a credential configures nothing"
    monkeypatch.setenv("OXYLABS_PASSWORD", "password")
    assert "oxylabs" in configured_live_data_providers()

    with WebRouter() as router:
        provider = router.get_live_data_provider("oxylabs")
        assert isinstance(provider, OxylabsLiveDataProvider) and provider.web_api is None
        with pytest.raises(LiveDataSourceNotFoundError):
            provider.live_data_source("amazon/product")  # a Web API scraper, with no Web API key to run it


def test_with_no_credential_the_error_names_both() -> None:
    with pytest.raises(ConfigurationError, match=r"OXYLABS_USERNAME and OXYLABS_PASSWORD.*OXYLABS_WEB_API_KEY"):
        OxylabsLiveDataProvider()


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("OXYLABS_USERNAME") and os.getenv("OXYLABS_PASSWORD")), reason="Oxylabs Web Scraper API not set"
)
def test_live_google_search() -> None:
    with WebRouter() as router:
        listed = router.live_data_sources(query="google search", provider="oxylabs")
        assert any(source.name == "google_search" for source in listed.sources)
        described = router.live_data_source("google_search", provider="oxylabs")
        assert described.input_schema and described.input_schema["required"] == ["query"]
        response = router.live_data("google_search", {"query": "web scraping"}, provider="oxylabs")
        assert response.records and response.records[0].get("results")

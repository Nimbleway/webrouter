"""Oxylabs live data over the Web Scraper API ("Classic"): what a run sends, and the dispatch between both APIs.

Shapes follow https://developers.oxylabs.io/products/web-scraper-api/targets,
read on 2026-11-17. Values here are synthetic; the answers are in
``tests/providers/_oxylabs_classic.py``.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from tests.providers._oxylabs import _answer, _scraped
from tests.providers._oxylabs_classic import BASE, classic, no_oxylabs_env_fixture, parsed  # noqa: F401  (fixture)
from web_router import WebRouter
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers.oxylabs_classic import OxylabsClassicLiveDataProvider, classic_sources
from web_router.providers.oxylabs_live_data_any import OxylabsLiveDataProvider
from web_router.registry import configured_live_data_providers
from web_router.types_live_data import LiveDataRequest

# -- run ----------------------------------------------------------------------


def test_a_run_posts_the_source_and_inputs_with_parse_and_signs_in_with_basic_auth() -> None:
    provider, api = classic()
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
    provider, api = classic()
    inputs = {"query": "dQw4w9WgXcQ", "language_code": "en", "callback_url": "https://example.com/hook"}
    provider.run(LiveDataRequest(source="youtube_subtitles", inputs=inputs, provider_options={"parse": False}))

    assert api.body() == {
        "source": "youtube_subtitles",
        "query": "dQw4w9WgXcQ",
        "context": [{"key": "language_code", "value": "en"}],
    }, "a caller's parse is never sent, and this source refuses it"


def test_a_source_answered_in_its_own_json_is_not_sent_parse_and_its_string_is_read() -> None:
    """``google_trends_explore`` refuses ``parse`` and sends its JSON as a string, as seen live."""
    provider, api = classic(parsed('{"interest_over_time": [{"keyword": "x"}]}'))
    response = provider.run(LiveDataRequest(source="google_trends_explore", inputs={"query": "x"}))
    assert "parse" not in api.body()
    assert response.records == [{"interest_over_time": [{"keyword": "x"}]}]


def test_a_source_that_requires_render_is_sent_it() -> None:
    provider, api = classic()
    provider.run(LiveDataRequest(source="google_ai_mode", inputs={"query": "best laptop"}))
    assert api.body()["render"] == "html"


def test_a_callers_render_does_not_replace_the_one_a_source_requires() -> None:
    provider, api = classic()
    options = {"render": "json", "geo_location": "United States"}
    provider.run(LiveDataRequest(source="google_ai_mode", inputs={"query": "best laptop"}, provider_options=options))
    assert api.body()["render"] == "html"
    assert api.body()["geo_location"] == "United States", "other options still pass through"


def test_an_unknown_source_is_not_found_before_anything_is_sent() -> None:
    provider, api = classic()
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="shopee_search", inputs={"query": "x"}))
    assert api.requests == []


# -- one `oxylabs` over both APIs --------------------------------------------


def _both(monkeypatch: pytest.MonkeyPatch) -> tuple[OxylabsLiveDataProvider, Api, Api]:
    classic_api = Api(lambda request: httpx.Response(200, json=parsed()))
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


def test_each_api_is_given_only_its_own_credentials() -> None:
    provider = OxylabsLiveDataProvider(username="user", api_key="password", web_api_key="web-key")
    assert provider.classic is not None and provider.web_api is not None
    assert "web_api_key" not in provider.classic._client_kwargs and "username" not in provider.web_api._client_kwargs

    stray = OxylabsClassicLiveDataProvider(username="user", api_key="password", web_api_key="web-key")
    with pytest.raises(ProviderError, match="unexpected keyword argument 'web_api_key'"):
        stray.run(LiveDataRequest(source="amazon_search", inputs={"query": "x"}))  # refused, not silently dropped


def test_with_both_credentials_each_api_signs_in_to_its_own_host() -> None:
    provider = OxylabsLiveDataProvider(username="user", api_key="password", web_api_key="web-key")
    assert provider.classic is not None and provider.web_api is not None
    classic_client, web_client = provider.classic._client_kwargs, provider.web_api._client_kwargs
    assert classic_client["base_url"] == BASE and classic_client["headers"]["Authorization"].startswith("Basic ")
    assert web_client["base_url"] == "https://webapi.oxylabs.io"
    assert web_client["headers"]["Authorization"] == "Bearer web-key"


def test_one_base_url_with_both_credentials_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="both credentials and one base_url"):
        OxylabsLiveDataProvider(username="user", api_key="password", web_api_key="web-key", base_url="https://x.test")


@pytest.mark.parametrize(
    "credential", [{"username": "user", "api_key": "password"}, {"web_api_key": "web-key"}], ids=["classic", "web"]
)
def test_one_credential_keeps_its_base_url(credential: dict[str, str]) -> None:
    provider = OxylabsLiveDataProvider(**credential, base_url="https://proxy.example.test")
    api = provider.classic or provider.web_api
    assert api is not None and api._client_kwargs["base_url"] == "https://proxy.example.test"


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

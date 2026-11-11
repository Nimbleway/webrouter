"""Oxylabs live data: scrapers run as jobs, the Web API key, and the live check.

Shapes follow https://developers.oxylabs.io/products/web-api (endpoint
discovery, dedicated scrapers, delivery modes), read on 2026-11-10. Values are
synthetic. Not yet verified live.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from tests.providers._oxylabs import BASE, PENDING, _answer, _job, _scraped, _sent
from web_router import WebRouter
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers.oxylabs_live_data import OxylabsLiveDataProvider
from web_router.types_live_data import LiveDataRequest


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

    # The username may come from OXYLABS_USERNAME rather than the entry: the api_key is still the password.
    monkeypatch.setenv("OXYLABS_USERNAME", "user")
    api = Api(_answer(lambda request: httpx.Response(500)))
    provider = wire(OxylabsLiveDataProvider(api_key="realtime-password"), api)
    provider.live_data_sources()
    assert api.last.headers["Authorization"] == "Bearer web-key"

    api = Api(_answer(lambda request: httpx.Response(500)))
    provider = wire(OxylabsLiveDataProvider(username="user", api_key="pw", web_api_key="explicit"), api)
    provider.live_data_sources()
    assert api.last.headers["Authorization"] == "Bearer explicit"


def test_with_no_web_api_key_the_error_names_web_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Realtime password in ``api_key`` is not a key here, so the error says where the Web API key goes."""
    monkeypatch.delenv("OXYLABS_WEB_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match=r"OXYLABS_WEB_API_KEY.*'web_api_key'"):
        OxylabsLiveDataProvider(api_key="realtime-password")


def test_one_oxylabs_config_entry_serves_extract_and_live_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extract and live data read the same ``provider_config["oxylabs"]`` entry, each taking its own key."""
    for name in ("OXYLABS_USERNAME", "OXYLABS_PASSWORD", "OXYLABS_WEB_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = {"oxylabs": {"username": "user", "api_key": "realtime-password", "web_api_key": "web-key"}}
    with WebRouter(provider_config=config) as router:
        extract = router.get_extract_provider("oxylabs")
        live = router.get_live_data_provider("oxylabs")
        assert extract._client.headers["Authorization"].startswith("Basic ")  # type: ignore[attr-defined]
        assert live._client.headers["Authorization"] == "Bearer web-key"  # type: ignore[attr-defined]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("OXYLABS_WEB_API_KEY"), reason="Oxylabs Web API key not set")
def test_live_amazon_product() -> None:
    with WebRouter() as router:
        described = router.live_data_source("amazon/product", provider="oxylabs")
        assert described.input_schema and "query" in described.input_schema["properties"]
        response = router.live_data("amazon/product", {"query": "B07FZ8S74R"}, provider="oxylabs")
        assert response.records and response.records[0].get("title")

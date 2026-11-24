"""Oxylabs live data over both APIs: a listing, and a source search, when the Web API half fails.

The Web Scraper API's sources ship in a data file, so a bad or expired Web API
key must not hide them. Values here are synthetic.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from tests.providers._oxylabs import _answer, _scraped
from tests.providers._oxylabs_classic import no_oxylabs_env_fixture, parsed  # noqa: F401  (fixture)
from web_router.constants_oxylabs_classic import OXYLABS_WEB_API_FAILURE
from web_router.exceptions import ProviderError
from web_router.live_data_search import asearch_live_data_sources, search_live_data_sources
from web_router.providers.oxylabs_classic import classic_sources
from web_router.providers.oxylabs_live_data_any import OxylabsLiveDataProvider
from web_router.types_live_data import LiveDataSourcePage

UNAUTHORIZED = {"message": "Unauthorized"}


def _web_api_answers(status: int) -> Api:
    if status == 200:
        return Api(_answer(lambda request: httpx.Response(200, json=_scraped())))
    return Api(lambda request: httpx.Response(status, json=UNAUTHORIZED))


def _provider(web_api_status: int, *, classic: bool = True) -> OxylabsLiveDataProvider:
    credentials = {"username": "user", "api_key": "password"} if classic else {}
    provider = OxylabsLiveDataProvider(**credentials, web_api_key="web-key")
    assert provider.web_api is not None
    wire(provider.web_api, _web_api_answers(web_api_status))
    if provider.classic is not None:
        wire(provider.classic, Api(lambda request: httpx.Response(200, json=parsed())))
    return provider


async def _listing(provider: OxylabsLiveDataProvider, mode: str) -> LiveDataSourcePage:
    if mode == "sync":
        return provider.live_data_sources()
    return await provider.alive_data_sources()


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_failed_web_api_listing_still_lists_the_web_scraper_apis_sources(
    mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _provider(401)
    with caplog.at_level(logging.WARNING, logger="web_router.providers.oxylabs_live_data_any"):
        page = await _listing(provider, mode)

    assert [source.name for source in page.sources] == list(classic_sources())
    assert len(page.sources) == 25
    assert "401" in page.failures[OXYLABS_WEB_API_FAILURE]
    assert page.total is None, "the whole catalogue is not known while one API is missing"
    assert any("Web API" in record.getMessage() and "401" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_working_web_api_lists_both_catalogues(mode: str) -> None:
    page = await _listing(_provider(200), mode)

    names = [source.name for source in page.sources]
    assert names[: len(classic_sources())] == list(classic_sources())
    assert "amazon/product" in names and page.total == len(names)
    assert page.failures == {}


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_with_only_the_web_api_its_failure_still_raises(mode: str) -> None:
    with pytest.raises(ProviderError, match="401"):
        await _listing(_provider(401, classic=False), mode)


def test_a_blank_credential_counts_as_none_by_the_providers_shared_rule() -> None:
    provider = OxylabsLiveDataProvider(username=" ", api_key="password", web_api_key="web-key")
    assert provider.classic is None and provider.web_api is not None


def _recovering_provider() -> tuple[OxylabsLiveDataProvider, list[bool]]:
    """Both credentials; the Web API answers 503 on its listing while ``down[0]`` holds, its scrapers after."""
    provider = _provider(200)
    down = [True]
    healthy = _answer(lambda request: httpx.Response(200, json=_scraped()))

    def respond(request: httpx.Request) -> httpx.Response:
        if down[0] and request.url.path == "/v1/scrapers":
            return httpx.Response(503, json={"message": "Service Unavailable"})
        return healthy(request)

    assert provider.web_api is not None
    wire(provider.web_api, Api(respond))
    return provider, down


async def _search(provider: OxylabsLiveDataProvider, mode: str) -> LiveDataSourcePage:
    if mode == "sync":
        return search_live_data_sources(provider, "amazon product")
    return await asearch_live_data_sources(provider, "amazon product")


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_search_while_the_web_api_is_down_names_it_and_is_not_kept(mode: str) -> None:
    provider, down = _recovering_provider()

    first = await _search(provider, mode)
    assert "amazon/product" not in first.names
    assert "503" in first.failures[OXYLABS_WEB_API_FAILURE]
    assert first.total is None, "a count of a catalogue missing one API would be wrong"

    down[0] = False
    second = await _search(provider, mode)
    assert "amazon/product" in second.names, "the partial read was not kept, so the recovered Web API is read"
    assert second.failures == {}
    assert second.total == len(second.names)


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_search_with_a_working_web_api_searches_both_catalogues_and_keeps_the_read(mode: str) -> None:
    provider, down = _recovering_provider()
    down[0] = False

    first = await _search(provider, mode)
    down[0] = True
    second = await _search(provider, mode)

    assert "amazon/product" in first.names and first.failures == {}
    assert first.total == len(first.names)
    assert second.names == first.names and second.failures == {}, "the whole read is kept for the next search"

"""Running in production: the package logger, each ``auto`` hop logged without keys or URLs, the sync router's
warning inside an event loop, the member in flight named on an async deadline, and lazy Tavily clients."""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider, FakeProvider
from web_router import AsyncWebRouter, DeadlineExceededError, ProviderError, WebRouter
from web_router.providers.tavily import TavilyProvider
from web_router.registry import (
    register_extract_provider,
    register_provider,
    unregister_extract_provider,
    unregister_provider,
)
from web_router.types import SearchRequest, SearchResponse

SECRET_QUERY = "https://docs.example/private/path?token=sk-secret"


class _Broken(FakeProvider):
    name = "broken"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "upstream 503")

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "upstream 503")


class _Hangs(FakeProvider):
    name = "hangs"

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")


class _Pages(FakeExtractProvider):
    name = "pages"


@pytest.fixture(autouse=True)
def _members() -> Any:
    register_provider("broken", _Broken)
    register_provider("hangs", _Hangs)
    register_provider("fake", FakeProvider)
    register_extract_provider("pages", _Pages)
    yield
    for name in ("broken", "hangs", "fake"):
        unregister_provider(name)
    unregister_extract_provider("pages")


def test_the_package_logger_has_a_null_handler_and_nothing_else() -> None:
    handlers = logging.getLogger("web_router").handlers
    assert [type(handler) for handler in handlers] == [logging.NullHandler]


def test_each_auto_hop_is_logged_with_backend_outcome_and_time(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="web_router")
    with WebRouter(provider_config={"auto": {"chain": ["broken", "fake"]}}) as router:
        router.search(SECRET_QUERY)
    hops = [record for record in caplog.records if record.name == "web_router.providers._hop"]
    assert [record.levelno for record in hops] == [logging.INFO, logging.DEBUG]
    failed, answered = (record.getMessage() for record in hops)
    assert failed.startswith("auto search: broken failed (upstream, status None) in ")
    assert answered.startswith("auto search: fake answered in ")
    assert all("sk-secret" not in record.getMessage() for record in caplog.records)


def test_an_extract_hop_names_the_hosts_never_the_paths(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="web_router")
    urls = ["https://a.example/x?key=sk-1", "https://a.example/y", "https://b.example/z"]
    with WebRouter(provider_config={"auto": {"chain": ["pages"]}}) as router:
        router.extract(urls)
    (hop,) = [record.getMessage() for record in caplog.records if record.name == "web_router.providers._hop"]
    assert hop.endswith(" for https://a.example and 1 more")
    assert "/x" not in hop and "sk-1" not in hop


def test_the_sync_router_warns_when_called_inside_a_running_loop() -> None:
    async def inside() -> None:
        with WebRouter("fake") as router, pytest.warns(RuntimeWarning, match=r"WebRouter\.search\(\) was called"):
            router.search("q")

    asyncio.run(inside())


def test_the_sync_router_outside_a_loop_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with WebRouter("fake") as router:
            router.search("q")


async def test_an_async_deadline_names_the_member_in_flight_and_those_before_it() -> None:
    async with AsyncWebRouter(provider_config={"auto": {"chain": ["broken", "hangs"]}}) as router:
        with pytest.raises(DeadlineExceededError) as caught:
            await router.search("q", timeout=0.2)
    message = str(caught.value)
    assert "broken: [broken] upstream 503" in message
    assert "hangs: Provider 'hangs' had not answered" in message


def test_tavily_builds_neither_client_until_a_call_needs_one() -> None:
    provider = TavilyProvider(api_key="test-key")
    assert provider._clients._sync is None and provider._clients._async is None
    assert provider._async_client is provider._async_client, "built once, then reused"
    assert provider._clients._sync is None, "an async call never builds the sync client"
    provider.close()  # closing what was never built is a no-op


def test_warmup_builds_each_auto_member_and_its_client_without_calling_it() -> None:
    with WebRouter(provider_config={"auto": {"chain": ["fake", "broken"]}}) as router:
        warmed = router.warmup()
        search = router.get_provider()
    assert warmed[:2] == ["fake", "broken"]
    assert set(search._members) == {0, 1}, "both members built"
    assert search._members[1].__class__ is _Broken

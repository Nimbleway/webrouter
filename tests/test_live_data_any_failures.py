"""A search over every live data backend when backends fail: the rest still answer, and all failing raises.

The backends are the in-memory ``fake`` and ``paged`` of
``tests/_live_data_any_fixtures.py``; no test reaches a real vendor.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from tests._live_data_any_fixtures import no_vendor_keys_fixture, two_backends_fixture  # noqa: F401  (fixtures)
from tests._live_data_fake import FakeLiveDataProvider
from tests._live_data_paged import PagedProvider
from web_router import AllProvidersFailedError, AsyncWebRouter, WebRouter
from web_router.exceptions import ProviderError
from web_router.registry import register_live_data_provider, unregister_live_data_provider
from web_router.types_live_data import LiveDataSourcePage


def test_a_backend_that_fails_is_left_out_and_named(two_backends: None) -> None:
    FakeLiveDataProvider.list_raises = ProviderError("fake", "HTTP 503")

    page = WebRouter().live_data_sources(limit=3)

    assert {source.provider for source in page.sources} == {"paged"}
    assert "fake" in page.failures and "503" in page.failures["fake"]
    # Part of the catalogue is missing, so no count is claimed for it.
    assert page.total is None


def test_every_backend_failing_raises_rather_than_answering_empty(fake_live_data_provider: Any) -> None:
    FakeLiveDataProvider.list_raises = ProviderError("fake", "HTTP 503")
    try:
        with pytest.raises(ProviderError, match="every live data backend failed"):
            WebRouter().live_data_sources()
    finally:
        FakeLiveDataProvider.list_raises = None


def _refused_key(provider: str) -> ProviderError:
    request = httpx.Request("GET", "https://api.example.com")
    response = httpx.Response(401, request=request)
    refused = httpx.HTTPStatusError("401", request=request, response=response)
    return ProviderError(provider, "HTTP 401", original=refused)


@pytest.mark.parametrize("use_async", [False, True])
def test_every_backend_failing_keeps_each_ones_error_and_kind(
    two_backends: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, use_async: bool
) -> None:
    FakeLiveDataProvider.list_raises = _refused_key("fake")
    paged_error = _refused_key("paged")

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise paged_error

    async def arefuse(*_args: Any, **_kwargs: Any) -> Any:
        raise paged_error

    monkeypatch.setattr(PagedProvider, "_list", refuse)
    monkeypatch.setattr(PagedProvider, "_alist", arefuse)
    with caplog.at_level(logging.WARNING, logger="web_router"), pytest.raises(AllProvidersFailedError) as caught:
        if use_async:
            asyncio.run(AsyncWebRouter().live_data_sources())
        else:
            WebRouter().live_data_sources()

    assert set(caught.value.failures) == {"fake", "paged"}
    assert caught.value.failures["paged"] is paged_error
    assert (caught.value.kind, caught.value.retryable) == ("auth", False)
    warned = {record.getMessage().split()[0] for record in caplog.records if record.levelno == logging.WARNING}
    assert warned == {"fake", "paged"}


class _HalfAnswered(PagedProvider):
    """A backend over two APIs whose second one did not answer: its own page names that, as oxylabs' does."""

    name = "half"

    def _list(self, *, limit: int | None, offset: int | None) -> LiveDataSourcePage:
        page = super()._list(limit=limit, offset=offset)
        return page.model_copy(update={"total": None, "failures": {"half second_api": "HTTP 503"}})


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("query", [None, "amazon"])
def test_a_backend_pages_own_failures_reach_the_merged_page(
    two_backends: None, use_async: bool, query: str | None
) -> None:
    register_live_data_provider("half", _HalfAnswered)
    try:
        if use_async:
            page = asyncio.run(AsyncWebRouter().live_data_sources(query=query))
        else:
            page = WebRouter().live_data_sources(query=query)
    finally:
        unregister_live_data_provider("half")

    assert page.sources, "the backends that answered still list their sources"
    assert page.failures.get("half second_api") == "HTTP 503"
    assert page.total is None, "part of one backend's catalogue is missing"

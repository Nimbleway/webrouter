"""The Tavily SDK's own exceptions carry no status, so each is given the kind its status would have."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("tavily", reason="tavily extra not installed")

from tavily import AsyncTavilyClient, TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    TavilyKeylessLimitError,
    UsageLimitExceededError,
)

from web_router.exceptions import ProviderError
from web_router.providers.tavily import TavilyExtractProvider, TavilyProvider
from web_router.types import ExtractRequest, SearchRequest

URL = "https://example.com"
CASES = [
    (InvalidAPIKeyError("Unauthorized: missing or invalid API key."), "auth"),
    (UsageLimitExceededError("Too many requests"), "rate_limit"),
    (ForbiddenError("This request exceeds your plan's set usage limit."), "rate_limit"),
    (ForbiddenError("Forbidden"), "auth"),
    (BadRequestError("Query is invalid."), "bad_request"),
]


def _raising(error: Exception) -> Any:
    def call(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    async def acall(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return call, acall


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")


@pytest.mark.parametrize(("error", "kind"), CASES)
def test_a_search_sdk_error_gets_its_kind(monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str) -> None:
    call, acall = _raising(error)
    monkeypatch.setattr(TavilyClient, "search", call)
    monkeypatch.setattr(AsyncTavilyClient, "search", acall)
    provider = TavilyProvider()
    with pytest.raises(ProviderError) as sync_raised:
        provider.search(SearchRequest(query="hi"))
    with pytest.raises(ProviderError) as async_raised:
        asyncio.run(provider.asearch(SearchRequest(query="hi")))
    for raised in (sync_raised, async_raised):
        assert raised.value.kind == kind
        assert raised.value.original is error
        assert raised.value.retryable is (kind == "rate_limit")


# The extract path maps errors through the same code as search, so one case covers its wrapper.
@pytest.mark.parametrize(("error", "kind"), [CASES[3]])
def test_an_extract_sdk_error_gets_its_kind(monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str) -> None:
    call, acall = _raising(error)
    monkeypatch.setattr(TavilyClient, "extract", call)
    monkeypatch.setattr(AsyncTavilyClient, "extract", acall)
    provider = TavilyExtractProvider()
    with pytest.raises(ProviderError) as sync_raised:
        provider.extract(ExtractRequest(urls=[URL]))
    with pytest.raises(ProviderError) as async_raised:
        asyncio.run(provider.aextract(ExtractRequest(urls=[URL])))
    assert sync_raised.value.kind == async_raised.value.kind == kind


def test_a_keyless_limit_is_a_rate_limit_with_its_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    error = TavilyKeylessLimitError("Keyless limit reached.", code="rate_limited", retry_after_seconds=30)
    monkeypatch.setattr(TavilyClient, "search", _raising(error)[0])
    with pytest.raises(ProviderError) as raised:
        TavilyProvider().search(SearchRequest(query="hi"))
    assert (raised.value.kind, raised.value.retry_after) == ("rate_limit", 30.0)


def test_any_other_error_is_classified_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TavilyClient, "search", _raising(ValueError("boom"))[0])
    with pytest.raises(ProviderError) as raised:
        TavilyProvider().search(SearchRequest(query="hi"))
    assert raised.value.kind == "upstream"

"""Nimble search and extract on Nimble's keyless tier: no credential, and no ``Authorization`` header sent."""

from __future__ import annotations

import asyncio
from typing import TypeVar

import httpx
import pytest

from web_router.exceptions import ConfigurationError
from web_router.types import ExtractRequest, SearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from nimble_python import AsyncNimble

from web_router.providers._nimble_clients import _NimbleKeyless
from web_router.providers.nimble import NimbleExtractProvider, NimbleProvider

_P = TypeVar("_P", bound=_NimbleKeyless)

URL = "https://example.com"
_EXTRACTED = {"url": URL, "status": "success", "data": {"markdown": "# Example"}}


def _transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _EXTRACTED if request.url.path.endswith("/extract") else {"results": [{"url": URL, "title": "A"}]}
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handle)


def _wired(provider: _P, seen: list[httpx.Request]) -> _P:
    """``provider`` with both its clients sending to a recorder instead of Nimble, credential left as it was."""
    assert provider._client is not None
    provider._client = provider._client.copy(http_client=httpx.Client(transport=_transport(seen)))
    provider._async_client = provider._async_client.copy(http_client=httpx.AsyncClient(transport=_transport(seen)))
    return provider


def test_a_keyless_search_sends_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)
    seen: list[httpx.Request] = []
    provider = _wired(NimbleProvider(allow_keyless=True), seen)

    response = provider.search(SearchRequest(query="q"))

    assert [result.url for result in response.results] == [URL]
    assert "authorization" not in seen[0].headers


def test_a_keyless_async_extract_sends_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)
    seen: list[httpx.Request] = []
    provider = _wired(NimbleExtractProvider(allow_keyless=True), seen)

    response = asyncio.run(provider.aextract(ExtractRequest(urls=[URL])))

    assert response.documents[0].content == "# Example"
    assert "authorization" not in seen[0].headers


def test_a_keyless_call_keeps_the_caller_s_own_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)
    seen: list[httpx.Request] = []
    provider = _wired(NimbleProvider(allow_keyless=True), seen)

    provider.search(SearchRequest(query="q", provider_options={"extra_headers": {"X-Trace": "t"}}))

    assert seen[0].headers["x-trace"] == "t"
    assert "authorization" not in seen[0].headers


def test_a_keyless_provider_does_not_retry_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)

    assert NimbleProvider(allow_keyless=True)._async_client.max_retries == 0
    assert NimbleExtractProvider(allow_keyless=True, max_retries=3)._async_client.max_retries == 3


@pytest.mark.parametrize("provider_class", [NimbleProvider, NimbleExtractProvider])
def test_a_key_in_the_environment_wins_over_the_keyless_tier(
    monkeypatch: pytest.MonkeyPatch, provider_class: type[NimbleProvider | NimbleExtractProvider]
) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    seen: list[httpx.Request] = []
    provider = _wired(provider_class(allow_keyless=True), seen)

    if isinstance(provider, NimbleExtractProvider):
        provider.extract(ExtractRequest(urls=[URL]))
    else:
        provider.search(SearchRequest(query="q"))

    assert seen[0].headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize("provider_class", [NimbleProvider, NimbleExtractProvider])
def test_a_missing_key_still_raises_unless_keyless_is_allowed(
    monkeypatch: pytest.MonkeyPatch, provider_class: type[NimbleProvider]
) -> None:
    """Naming Nimble without a key says so, rather than quietly running on the shared keyless allowance."""
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="NIMBLE_API_KEY"):
        provider_class()


@pytest.mark.parametrize("provider_class", [NimbleProvider, NimbleExtractProvider])
def test_a_key_beside_a_client_still_raises_with_keyless_allowed(provider_class: type[_NimbleKeyless]) -> None:
    """The conflict is the caller's to fix; going keyless would drop both credentials."""
    with pytest.raises(ConfigurationError):
        provider_class(allow_keyless=True, api_key="k", async_client=AsyncNimble(api_key="end-user-key"))


@pytest.mark.integration
def test_live_keyless_search_and_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)
    search, extract = NimbleProvider(allow_keyless=True), NimbleExtractProvider(allow_keyless=True)
    try:
        results = search.search(SearchRequest(query="python asyncio tutorial", max_results=3)).results
        documents = extract.extract(ExtractRequest(urls=[URL])).documents
    finally:
        search.close()
        extract.close()

    assert results
    assert "documentation examples" in documents[0].content

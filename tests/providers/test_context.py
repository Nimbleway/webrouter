"""Context mapping and failure boundaries; synthetic responses contain no real credential."""

from __future__ import annotations

import json
import os
import traceback
from contextlib import closing
from typing import Any

import httpx
import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.context import ContextProvider
from web_router.types import SearchRequest

KEY = "synthetic-context-quote'\\key"
BODY = {
    "results": [
        {
            "url": "https://example.com/a",
            "title": "Source",
            "description": "Search description",
            "highlights": {"code": "SUCCESS", "highlights": ["Relevant passage", "Second passage"]},
            "relevance": "high",
        }
    ],
    "request_id": "synthetic-request",
    "partial": True,
}


def provider(body: Any = BODY, status: int = 200) -> ContextProvider:
    return ContextProvider(api_key=KEY, transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)))


def test_public_router_uses_highlights_and_preserves_unmapped_data() -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=BODY)

    with WebRouter(
        provider="context", provider_config={"context": {"api_key": KEY, "transport": httpx.MockTransport(answer)}}
    ) as router:
        result = router.search("public query", max_results=3)
    assert sent[0].url == "https://api.context.dev/v1/web/search"
    assert sent[0].headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(sent[0].content) == {
        "query": "public query",
        "numResults": 3,
        "highlightsOptions": {"enabled": True},
    }
    assert result.provider == "context" and result.request_id == "synthetic-request"
    assert result.results[0].content == "Relevant passage\nSecond passage"
    assert result.results[0].description == "Search description"
    assert result.results[0].score is None and result.results[0].published_date is None
    assert result.raw == BODY and result.raw["partial"] is True


async def test_async_router_uses_the_same_normalization() -> None:
    async with AsyncWebRouter(
        provider="context",
        provider_config={
            "context": {"api_key": KEY, "transport": httpx.MockTransport(lambda _: httpx.Response(200, json=BODY))}
        },
    ) as router:
        result = await router.search("public query")
    assert result.results[0].content == "Relevant passage\nSecond passage"


def test_unset_count_stays_unset_and_oversize_count_is_capped() -> None:
    with closing(provider()) as client:
        assert "numResults" not in client._build_payload(SearchRequest(query="q"))
        assert client._build_payload(SearchRequest(query="q", max_results=500))["numResults"] == 100


def test_no_credential_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        ContextProvider()


def test_unmapped_filter_is_rejected() -> None:
    with closing(provider()) as client, pytest.raises(UnsupportedParameterError):
        client.search(SearchRequest(query="q", country="US"))


@pytest.mark.parametrize("code", ["TIMEOUT", "WEBSITE_ACCESS_ERROR", "NOT_REQUESTED", "SUCCESS"])
def test_unavailable_or_empty_highlights_use_only_the_search_description(code: str) -> None:
    body = {
        "results": [
            {
                "url": "https://example.com",
                "description": "Only a snippet",
                "highlights": {"code": code, "highlights": []},
            }
        ]
    }
    with closing(provider(body)) as client:
        result = client.search(SearchRequest(query="q"))
    assert result.results[0].content == "Only a snippet" and result.raw == body


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"results": None},
        {"results": ["invalid"]},
        {"results": [{"url": "https://example.com", "highlights": {"code": KEY}}]},
    ],
)
def test_malformed_success_is_a_safe_failure(body: Any) -> None:
    with closing(provider(body)) as client, pytest.raises(ProviderError) as caught:
        client.search(SearchRequest(query="q"))
    assert KEY not in "".join(traceback.format_exception(caught.value))
    assert caught.value.original is None and caught.value.__cause__ is None


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_http_error_payload_and_exception_chains_do_not_reveal_credentials(status: int) -> None:
    with closing(provider({"error": f"Rejected {KEY!r}"}, status)) as client, pytest.raises(ProviderError) as caught:
        client.search(SearchRequest(query="q"))
    text = "".join(traceback.format_exception(caught.value))
    assert KEY not in text and repr(KEY)[1:-1] not in text
    assert caught.value.original is None and caught.value.__cause__ is None and caught.value.__context__ is None
    assert caught.value.status_code == status


def test_transport_error_is_not_reflected() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Transport saw {KEY!r}", request=request)

    with (
        closing(ContextProvider(api_key=KEY, transport=httpx.MockTransport(fail))) as client,
        pytest.raises(ProviderError) as caught,
    ):
        client.search(SearchRequest(query="q"))
    assert repr(KEY)[1:-1] not in "".join(traceback.format_exception(caught.value))
    assert caught.value.original is None


def test_empty_success_remains_empty() -> None:
    with closing(provider({"results": []})) as client:
        assert client.search(SearchRequest(query="q")).results == []


@pytest.mark.integration
def test_live_context_search_returns_query_relevant_content() -> None:
    if not os.environ.get("CONTEXT_DEV_API_KEY"):
        pytest.skip("CONTEXT_DEV_API_KEY not set")
    with WebRouter(provider="context") as router:
        result = router.search("PostgreSQL CREATE INDEX documentation", max_results=3)
    assert result.results and all(r.url.startswith("https://") and r.content for r in result.results)
    assert result.raw and any(r.get("highlights", {}).get("code") == "SUCCESS" for r in result.raw["results"])

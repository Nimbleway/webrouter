"""Exa MCP provider tests.

Checked keyless against ``mcp.exa.ai/mcp?tools=web_search_advanced_exa`` on
2026-11-09: ``tools/list`` gave the tool's schema, and a lone ``tools/call``
answered as server-sent events carrying Exa's REST JSON in one text block. The
reply below is a trimmed recording of a ``lite`` call with a domain, a date and
a location set.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers._clock import pinned_today
from web_router.providers.exa_mcp import ExaMcpProvider
from web_router.types import SearchRequest

REPLY = {
    "requestId": "5761a1b1e24d858fa2366a2e8abe8795",
    "resolvedSearchType": "",
    "results": [
        {
            "id": "https://en.wikipedia.org/wiki/Retrieval-augmented_generation",
            "url": "https://en.wikipedia.org/wiki/Retrieval-augmented_generation",
            "publishedDate": "2026-08-26T16:47:32.000Z",
            "title": "Retrieval-augmented generation",
            "highlights": ["Retrieval-augmented generation (RAG) is a technique that enables large language models"],
        }
    ],
    "searchTime": 208.5,
}


def _sse(result: dict[str, object]) -> httpx.Response:
    body = "event: message\ndata: " + json.dumps({"result": result, "jsonrpc": "2.0", "id": 1}) + "\n\n"
    return httpx.Response(200, text=body, request=httpx.Request("POST", "https://mcp.exa.ai/mcp"))


def _text(text: str) -> httpx.Response:
    return _sse({"content": [{"type": "text", "text": text}]})


@pytest.fixture
def provider() -> ExaMcpProvider:
    return ExaMcpProvider()


def test_the_search_calls_the_advanced_tool_named_on_the_url(provider: ExaMcpProvider) -> None:
    kwargs = provider._call_kwargs(SearchRequest(query="hi", max_results=3))
    call = kwargs["json"]
    assert kwargs["params"] == {"tools": "web_search_advanced_exa"}
    assert (call["method"], call["params"]["name"]) == ("tools/call", "web_search_advanced_exa")
    # No objective: the advanced tool refuses any argument it does not declare.
    assert call["params"]["arguments"] == {"query": "hi", "numResults": 3, "enableHighlights": True}
    assert "Authorization" not in provider._client_kwargs["headers"]


def test_filters_location_and_recency_reach_the_tool(provider: ExaMcpProvider) -> None:
    request = SearchRequest(
        query="hi", include_domains=["a.com"], exclude_domains=["b.com"], country="DE", recency="month"
    )
    with pinned_today(date(2026, 11, 9)):
        arguments = provider._build_payload(request)
    assert arguments == {
        "query": "hi",
        "enableHighlights": True,
        "includeDomains": ["a.com"],
        "excludeDomains": ["b.com"],
        "userLocation": "DE",
        "startPublishedDate": "2026-10-10",
    }


@pytest.mark.parametrize(
    ("depth", "contents"),
    [
        ("lite", {"enableHighlights": True, "highlightsMaxCharacters": 500}),
        ("fast", {"textMaxCharacters": 3000}),
        ("deep", {}),
    ],
)
def test_each_depth_sends_the_content_arguments_of_its_size(
    provider: ExaMcpProvider, depth: str, contents: dict
) -> None:
    request = SearchRequest(query="hi", search_depth=depth)  # type: ignore[arg-type]
    provider.validate(request)
    assert provider._build_payload(request) == {"query": "hi", **contents}


@pytest.mark.parametrize(("effort", "sent"), [("low", "lite"), ("medium", None), ("high", None), ("max", None)])
def test_effort_collapses_onto_lite_and_the_default(provider: ExaMcpProvider, effort: str, sent: str | None) -> None:
    """The tool has lite and auto (its default), so every level above low sends nothing."""
    assert provider._build_payload(SearchRequest(query="hi", effort=effort)).get("type") == sent  # type: ignore[arg-type]


def test_locale_is_refused_unsent(provider: ExaMcpProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="locale"):
        provider.validate(SearchRequest(query="hi", locale="en"))


def test_the_json_reply_is_normalized_as_exa_rest_is(provider: ExaMcpProvider) -> None:
    payload = provider._payload(_text(json.dumps(REPLY)))
    response = provider._to_response(SearchRequest(query="rag"), payload)
    (result,) = response.results

    assert response.request_id == "5761a1b1e24d858fa2366a2e8abe8795"
    assert result.url.startswith("https://en.wikipedia.org/") and result.title == "Retrieval-augmented generation"
    assert result.content.startswith("Retrieval-augmented generation (RAG)")
    assert result.published_date == "2026-08-26T16:47:32.000Z"
    assert result.score is None


def test_a_tool_error_is_a_provider_error(provider: ExaMcpProvider) -> None:
    with pytest.raises(ProviderError, match="MCP call failed"):
        provider._payload(_sse({"isError": True, "content": [{"type": "text", "text": "rate limited"}]}))


@pytest.mark.parametrize("text", ["No results found for your query.", json.dumps({"results": []})])
def test_a_reply_with_no_result_is_a_failure_auto_can_move_past(provider: ExaMcpProvider, text: str) -> None:
    with pytest.raises(ProviderError, match="no result"):
        provider._payload(_text(text))


# -- Live, keyless --------------------------------------------------------------


@pytest.mark.integration
def test_live_keyless_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("exa-mcp") as router:
        response = router.search("Who directed the 2005 film Pride and Prejudice?", max_results=3)

    assert response.provider == "exa-mcp"
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") and result.content for result in response.results)


@pytest.mark.integration
@pytest.mark.parametrize(("depth", "least", "most"), [("lite", 1, 600), ("fast", 1, 3000), ("deep", 3001, None)])
def test_live_each_depth_comes_back_at_its_size(depth: str, least: int, most: int | None) -> None:
    from web_router import WebRouter

    with WebRouter("exa-mcp") as router:
        response = router.search("what is retrieval augmented generation", max_results=2, search_depth=depth)

    sizes = [len(result.content) for result in response.results]
    assert sizes and max(sizes) >= least
    assert most is None or max(sizes) <= most


@pytest.mark.integration
def test_live_filters_are_honoured_at_low_effort() -> None:
    from web_router import WebRouter

    with WebRouter("exa-mcp") as router:
        response = router.search(
            "retrieval augmented generation", max_results=3, include_domains=["wikipedia.org"], effort="low"
        )

    assert response.results and all("wikipedia.org" in result.url for result in response.results)

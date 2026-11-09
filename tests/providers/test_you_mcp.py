"""You.com over MCP: the JSON-RPC envelope, the event stream, and keyless access."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.you_mcp import YouMcpProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider() -> YouMcpProvider:
    return YouMcpProvider()


def _sse(*messages: dict[str, Any]) -> httpx.Response:
    body = "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in messages)
    return httpx.Response(200, text=body, request=httpx.Request("POST", "https://api.you.com/mcp"))


def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


def test_no_credential_is_required() -> None:
    """The point of this provider: it constructs with no key in the environment."""
    assert YouMcpProvider().api_key is None


def test_the_search_is_wrapped_in_a_jsonrpc_tool_call(provider: YouMcpProvider) -> None:
    body = provider._call_kwargs(SearchRequest(query="hi", max_results=3))["json"]

    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "you-search"
    assert body["params"]["arguments"] == {"query": "hi", "count": 3}


@pytest.mark.parametrize(
    ("depth", "expected"),
    [("lite", "none"), ("fast", "highlights"), ("deep", "full_page")],
)
def test_depth_maps_onto_the_bare_extraction_enum(provider: YouMcpProvider, depth: str, expected: str) -> None:
    """The MCP tool rejects the object form the REST API wants."""
    assert provider._build_payload(SearchRequest(query="hi", search_depth=depth))["extraction"] == expected


def test_lite_says_none_rather_than_staying_silent(provider: YouMcpProvider) -> None:
    """This endpoint extracts highlights by default, so lite has to opt out explicitly."""
    assert provider._build_payload(SearchRequest(query="hi", search_depth="lite"))["extraction"] == "none"


def test_filters_go_inline_or_as_tool_arguments(provider: YouMcpProvider) -> None:
    """you-search has no country, language or include_domains argument: it maps loc:, lang: and site: itself."""
    request = SearchRequest(
        query="hi", include_domains=["a.com", "b.org"], locale="pt-BR", country="br", recency="month"
    )
    assert provider._build_payload(request) == {
        "query": "hi site:a.com site:b.org lang:PT-BR loc:BR",
        "freshness": "month",
    }
    assert provider._build_payload(SearchRequest(query="hi", exclude_domains=["b.com"])) == {
        "query": "hi",
        "exclude_domains": ["b.com"],
    }


def test_values_the_rest_api_refuses_are_refused_here_too(
    provider: YouMcpProvider, transport: httpx.MockTransport, sent: list[httpx.Request]
) -> None:
    """The tool drops an unknown loc: or lang: in silence, so the REST lists hold here, before anything is sent."""
    provider = YouMcpProvider(transport=transport)
    for request in (
        SearchRequest(query="hi", country="ZZ"),
        SearchRequest(query="hi", locale="pt"),
        SearchRequest(query="hi", include_domains=["a.com"], exclude_domains=["b.com"]),
        SearchRequest(query="hi", effort="max"),
    ):
        with pytest.raises(UnsupportedParameterError):
            provider.search(request)
    assert not sent


def test_the_payload_is_unwrapped_from_the_event_stream(provider: YouMcpProvider) -> None:
    payload = {"results": {"web": [{"url": "https://a.com", "title": "A", "description": "d"}]}}
    response = _sse(
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "searching"}},
        _tool_result(payload),
    )

    assert provider._payload(response) == payload


def test_progress_notifications_do_not_masquerade_as_the_answer(provider: YouMcpProvider) -> None:
    """The stream carries log lines before the reply; only the reply has a result."""
    payload = {"results": {"web": []}}
    response = _sse(
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "one"}},
        {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "two"}},
        _tool_result(payload),
    )

    assert provider._payload(response) == payload


def test_a_tool_error_becomes_a_provider_error(provider: YouMcpProvider) -> None:
    """MCP reports a rejected call in a 200 body, so it has to be read, not inferred from status."""
    response = _sse({"result": {"isError": True, "content": [{"type": "text", "text": "Input validation error"}]}})

    with pytest.raises(ProviderError, match="Input validation error"):
        provider._payload(response)


def test_a_jsonrpc_error_becomes_a_provider_error(provider: YouMcpProvider) -> None:
    response = _sse({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params"}})

    with pytest.raises(ProviderError, match="Invalid params"):
        provider._payload(response)


def test_a_stream_with_no_result_is_an_error_not_an_empty_response(provider: YouMcpProvider) -> None:
    with pytest.raises(ProviderError):
        provider._payload(_sse({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}))


def test_structured_content_without_text_is_no_result(provider: YouMcpProvider) -> None:
    """Only AgentCore reads structuredContent; for the others it still means nothing to parse."""
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [], "structuredContent": {"results": []}}}

    with pytest.raises(ProviderError, match="no result payload"):
        provider._payload(_sse(reply))


def test_normalization_is_inherited_from_the_rest_provider(provider: YouMcpProvider) -> None:
    """Same payload shape as the REST API, so the mapping must not be duplicated."""
    payload = {
        "results": {
            "web": [
                {
                    "url": "https://a.com",
                    "title": "A",
                    "description": "a snippet",
                    "contents": {"highlights": ["one", "two"]},
                    "page_age": "2026-01-01",
                }
            ]
        }
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)

    assert response.provider == "you-mcp"
    result = response.results[0]
    assert result.description == "a snippet"
    assert result.content == "one\n\ntwo"
    assert result.published_date == "2026-01-01"


@pytest.mark.integration
def test_live_keyless_search_returns_results() -> None:
    """No skipif: this endpoint needs no credential, which is the whole point."""
    from web_router import WebRouter

    with WebRouter("you-mcp") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.results
    assert all(result.url for result in response.results)
    assert response.results[0].content


@pytest.mark.integration
def test_live_inline_operators_are_taken_out_of_the_query() -> None:
    """Look at raw metadata.query: site:, lang: and loc: must be gone from it, and every result on the site."""
    from web_router import WebRouter

    with WebRouter("you-mcp") as router:
        response = router.search(
            "asyncio tutorial", max_results=5, include_domains=["docs.python.org"], locale="en", country="US"
        )

    assert response.results
    assert all("docs.python.org" in result.url for result in response.results)
    assert response.raw["metadata"]["query"] == "asyncio tutorial"

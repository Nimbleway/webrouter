"""Exa MCP provider tests.

The request and the text-block reply were checked live against
``mcp.exa.ai/mcp`` on 2026-10-01 with no credential: a lone ``tools/call`` of
``web_search_exa`` with ``query``, ``objective`` and ``numResults`` returned that
many results as server-sent events. The reply below is a trimmed recording.
"""

from __future__ import annotations

import json

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.exa_mcp import ExaMcpProvider
from web_router.types import SearchRequest

TEXT = (
    "Title: Pride & Prejudice (2005 film)\n"
    "URL: https://en.wikipedia.org/wiki/Pride_%26_Prejudice_(2005_film)\n"
    "Published: N/A\n"
    "Author: N/A\n"
    "Highlights:\n"
    "| Directed by | Joe Wright |\n"
    "Title: a highlight line that only looks like a new result\n"
    "\n"
    "Title: Pride & Prejudice (2005) - AFI Catalog\n"
    "URL: https://catalog.afi.com/Catalog/moviedetails/62735\n"
    "Published: 2005-11-11T00:00:00.000Z\n"
    "Author: AFI\n"
    "Highlights:\n"
    "Director: Joe Wright"
)


def _sse(result: dict[str, object]) -> httpx.Response:
    body = "event: message\ndata: " + json.dumps({"result": result, "jsonrpc": "2.0", "id": 1}) + "\n\n"
    return httpx.Response(200, text=body, request=httpx.Request("POST", "https://mcp.exa.ai/mcp"))


@pytest.fixture
def provider() -> ExaMcpProvider:
    return ExaMcpProvider()


def test_the_search_is_one_tools_call_with_the_query_as_its_objective(provider: ExaMcpProvider) -> None:
    call = provider._call_kwargs(SearchRequest(query="hi", max_results=3))["json"]
    assert (call["method"], call["params"]["name"]) == ("tools/call", "web_search_exa")
    assert call["params"]["arguments"] == {"query": "hi", "objective": "hi", "numResults": 3}
    assert "Authorization" not in provider._client_kwargs["headers"]


def test_text_blocks_become_results_and_na_stays_unset(provider: ExaMcpProvider) -> None:
    payload = provider._payload(_sse({"content": [{"type": "text", "text": TEXT}]}))
    response = provider._to_response(SearchRequest(query="who directed it"), payload)
    first, second = response.results

    assert len(response.results) == 2, "a highlight starting with 'Title: ' must not split a result"
    assert first.url.startswith("https://en.wikipedia.org/") and first.title == "Pride & Prejudice (2005 film)"
    assert "Joe Wright" in first.content and "only looks like a new result" in first.content
    assert first.published_date is None and "author" not in first.extra
    assert first.score is None
    assert second.published_date == "2005-11-11T00:00:00.000Z" and second.extra["author"] == "AFI"


def test_a_tool_error_is_a_provider_error(provider: ExaMcpProvider) -> None:
    with pytest.raises(ProviderError, match="MCP call failed"):
        provider._payload(_sse({"isError": True, "content": [{"type": "text", "text": "rate limited"}]}))


def test_depth_is_refused_rather_than_dropped(provider: ExaMcpProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="search_depth"):
        provider.validate(SearchRequest(query="hi", search_depth="deep"))


@pytest.mark.integration
def test_live_keyless_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("exa-mcp") as router:
        response = router.search("Who directed the 2005 film Pride and Prejudice?", max_results=3)

    assert response.provider == "exa-mcp"
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") and result.content for result in response.results)


def test_text_that_is_not_a_result_block_is_dropped(provider: ExaMcpProvider) -> None:
    payload = provider._payload(_sse({"content": [{"type": "text", "text": "Search results for: who\n\n" + TEXT}]}))
    assert [hit["url"] for hit in payload["results"]] == [
        "https://en.wikipedia.org/wiki/Pride_%26_Prejudice_(2005_film)",
        "https://catalog.afi.com/Catalog/moviedetails/62735",
    ]


def test_a_reply_with_no_result_is_a_failure_auto_can_move_past(provider: ExaMcpProvider) -> None:
    with pytest.raises(ProviderError, match="no result"):
        provider._payload(_sse({"content": [{"type": "text", "text": "No results found for your query."}]}))

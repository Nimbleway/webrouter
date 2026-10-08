"""Parallel MCP provider tests.

Checked live against ``search.parallel.ai/mcp`` on 2026-10-01 with no
credential: a lone ``tools/call`` of ``web_search`` answers plain JSON whose text
is the REST ``/v1beta/search`` payload, and it ignores a result count -- asked
for 3 it returned 10 -- which is why ``max_results`` is applied to the response.
"""

from __future__ import annotations

import json

import httpx
import pytest

from web_router.providers.parallel_mcp import ParallelMcpProvider
from web_router.types import SearchRequest

PAYLOAD = {
    "search_id": "search_57c8",
    "results": [
        {"url": f"https://example.com/{i}", "title": f"Result {i}", "publish_date": None, "excerpts": [f"excerpt {i}"]}
        for i in range(10)
    ],
}


def _json_reply(payload: dict[str, object]) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "_meta": {"parallel/usage": [{"name": "sku_search", "count": 1, "cost_usd": 0.001}]},
            "content": [{"type": "text", "text": json.dumps(payload)}],
        },
    }
    return httpx.Response(200, json=body, request=httpx.Request("POST", "https://search.parallel.ai/mcp"))


@pytest.fixture
def provider() -> ParallelMcpProvider:
    return ParallelMcpProvider()


def test_the_search_sends_no_result_count_the_tool_would_ignore(provider: ParallelMcpProvider) -> None:
    call = provider._call_kwargs(SearchRequest(query="hi", max_results=3))["json"]
    assert call["params"] == {"name": "web_search", "arguments": {"objective": "hi", "search_queries": ["hi"]}}
    assert "Authorization" not in provider._client_kwargs["headers"]


def test_max_results_keeps_the_first_results_and_raw_keeps_them_all(provider: ParallelMcpProvider) -> None:
    payload = provider._payload(_json_reply(PAYLOAD))
    response = provider._to_response(SearchRequest(query="hi", max_results=3), payload)

    assert [r.url for r in response.results] == [
        "https://example.com/0",
        "https://example.com/1",
        "https://example.com/2",
    ]
    assert len(response.raw["results"]) == 10
    assert response.request_id == "search_57c8"
    uncapped = provider._to_response(SearchRequest(query="hi"), payload)
    assert len(uncapped.results) == 10


@pytest.mark.integration
def test_live_keyless_search_returns_at_most_max_results() -> None:
    from web_router import WebRouter

    with WebRouter("parallel-mcp") as router:
        response = router.search("Who directed the 2005 film Pride and Prejudice?", max_results=3)

    assert response.provider == "parallel-mcp"
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") and result.content for result in response.results)

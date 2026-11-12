"""Exa, Tavily and Parallel search through a LiteLLM proxy.

Checked live against a LiteLLM proxy (``/v1/search/{exa,tavily,parallel}``, tools ``exa_ai``, ``tavily`` and
``parallel_ai``) on 2026-11-12: each vendor's own fields reached it and changed the results (Exa ``type=deep-lite``
with ``excludeDomains``, Tavily ``search_depth=advanced`` with ``time_range``, Parallel ``mode=advanced`` with a
``source_policy``), and every reply came back in the shape recorded below, trimmed.
"""

from __future__ import annotations

import json
import os
from datetime import date

import httpx
import pytest

from web_router.exceptions import ConfigurationError
from web_router.providers._clock import pinned_today
from web_router.providers.litellm import ExaLiteLLMProvider, ParallelLiteLLMProvider, TavilyLiteLLMProvider
from web_router.types import SearchRequest

PROXY = "https://litellm.example.com"
REPLY = {
    "object": "search",
    "results": [
        {
            "title": "Retrieval-augmented generation",
            "url": "https://en.wikipedia.org/wiki/Retrieval-augmented_generation",
            "snippet": "Retrieval-augmented generation (RAG) is a technique that enables large language models",
            "date": "2026-08-26",
            "last_updated": "2026-11-01",
            "excerpts": ["Retrieval-augmented generation (RAG) is a technique", "It was introduced in 2020."],
        }
    ],
}
LIVE = pytest.mark.skipif(
    not (os.getenv("LITELLM_PROXY_API_KEY") and os.getenv("LITELLM_PROXY_API_BASE")),
    reason="LITELLM_PROXY_API_KEY and LITELLM_PROXY_API_BASE not set",
)


def _recording(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=REPLY)

    return httpx.MockTransport(handle)


@pytest.mark.parametrize(
    ("cls", "tool"),
    [(ExaLiteLLMProvider, "exa"), (TavilyLiteLLMProvider, "tavily"), (ParallelLiteLLMProvider, "parallel")],
)
def test_a_search_goes_to_the_proxy_tool_with_the_proxy_key(cls: type, tool: str) -> None:
    seen: list[httpx.Request] = []
    provider = cls(api_key="litellm-key", base_url=PROXY + "/", transport=_recording(seen))

    response = provider.search(SearchRequest(query="what is rag", max_results=3))

    (sent,) = seen
    assert str(sent.url) == f"{PROXY}/v1/search/{tool}"
    assert sent.headers["Authorization"] == "Bearer litellm-key"
    body = json.loads(sent.content)
    assert (body["query"], body["max_results"]) == ("what is rag", 3)
    assert response.provider == cls.name and len(response.results) == 1


def test_the_operator_can_name_the_tool_otherwise() -> None:
    seen: list[httpx.Request] = []
    ExaLiteLLMProvider(api_key="k", base_url=PROXY, search_tool="exa-search", transport=_recording(seen)).search(
        SearchRequest(query="q")
    )
    assert seen[0].url.path == "/v1/search/exa-search"


def test_the_vendor_mapping_is_sent_whole() -> None:
    """``effort`` and the filters arrive as the vendor's own fields, which the proxy forwards."""
    request = SearchRequest(query="q", max_results=3, effort="high", exclude_domains=["b.com"], recency="week")
    with pinned_today(date(2026, 11, 12)):
        exa = ExaLiteLLMProvider(api_key="k", base_url=PROXY)._build_payload(request)
        parallel = ParallelLiteLLMProvider(api_key="k", base_url=PROXY)._build_payload(request)
    assert (exa["type"], exa["excludeDomains"], exa["numResults"]) == ("deep-lite", ["b.com"], 3)
    assert exa["startPublishedDate"].startswith("2026-11-05")
    # Parallel's body has no query of its own; LiteLLM's sits beside its objective.
    assert (parallel["mode"], parallel["objective"], parallel["query"]) == ("advanced", "q", "q")


def test_tavily_sends_its_mapping_without_the_sdk_timeout() -> None:
    body = TavilyLiteLLMProvider(api_key="k", base_url=PROXY)._build_payload(
        SearchRequest(query="q", search_depth="fast", recency="month")
    )
    assert body == {"query": "q", "search_depth": "advanced", "time_range": "month", "filter_by_published_date": True}


def test_without_a_proxy_url_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_PROXY_API_BASE", raising=False)
    seen: list[httpx.Request] = []
    provider = ExaLiteLLMProvider(api_key="k", transport=_recording(seen))

    with pytest.raises(ConfigurationError, match="LITELLM_PROXY_API_BASE"):
        provider.search(SearchRequest(query="q"))
    assert seen == []


def test_the_reply_is_normalized_the_same_for_every_vendor() -> None:
    response = ParallelLiteLLMProvider(api_key="k", base_url=PROXY)._to_response(SearchRequest(query="rag"), REPLY)
    (result,) = response.results

    assert result.url.startswith("https://en.wikipedia.org/") and result.title == "Retrieval-augmented generation"
    assert result.description.startswith("Retrieval-augmented generation (RAG)")
    assert "introduced in 2020" in result.content
    assert result.published_date == "2026-08-26"
    assert result.extra == {"last_updated": "2026-11-01"}


def test_tavily_extras_land_where_native_tavily_puts_them() -> None:
    """Score, the full page and the answer, which LiteLLM documents for Tavily (``raw_content`` and ``score`` on a
    result, ``answer`` and ``request_id`` under ``provider_metadata``), fill the same fields ``tavily`` fills."""
    hit = {"url": "https://a.com", "title": "A", "snippet": "short", "raw_content": "the whole page", "score": 0.83}
    payload = {"results": [hit], "provider_metadata": {"answer": "RAG is...", "request_id": "req-1"}}
    response = TavilyLiteLLMProvider(api_key="k", base_url=PROXY)._to_response(SearchRequest(query="q"), payload)
    (result,) = response.results

    assert (response.answer, response.request_id) == ("RAG is...", "req-1")
    assert (result.content, result.description, result.score) == ("the whole page", "short", 0.83)
    assert result.extra == {}


@pytest.mark.parametrize(
    "url",
    [
        "http://proxy.example.com",
        "https://user:secret@proxy.example.com",
        "https://proxy.example.com?key=secret",
        "ftp://proxy.example.com",
    ],
)
def test_an_unsafe_proxy_url_never_gets_the_key(url: str) -> None:
    seen: list[httpx.Request] = []
    provider = ExaLiteLLMProvider(api_key="k", base_url=url, transport=_recording(seen))

    with pytest.raises(ConfigurationError, match="LITELLM_PROXY_API_BASE") as raised:
        provider.search(SearchRequest(query="q"))
    assert seen == []
    assert "secret" not in str(raised.value)


def test_plain_http_is_allowed_to_this_machine() -> None:
    seen: list[httpx.Request] = []
    ExaLiteLLMProvider(api_key="k", base_url="http://localhost:4000", transport=_recording(seen)).search(
        SearchRequest(query="q")
    )
    assert str(seen[0].url) == "http://localhost:4000/v1/search/exa"


# -- Live, through a LiteLLM proxy ----------------------------------------------------------------------------------


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "tavily-litellm", "parallel-litellm"])
def test_live_search_returns_results(name: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("Who directed the 2005 film Pride and Prejudice?", max_results=3)

    assert response.provider == name
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") and result.content for result in response.results)


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "tavily-litellm", "parallel-litellm"])
def test_live_a_domain_filter_is_honoured(name: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("retrieval augmented generation", max_results=3, include_domains=["wikipedia.org"])

    assert response.results and all("wikipedia.org" in result.url for result in response.results)


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "parallel-litellm"])
@pytest.mark.parametrize("effort", ["low", "high"])
def test_live_every_effort_is_accepted(name: str, effort: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("what is retrieval augmented generation", max_results=2, effort=effort)

    assert response.results


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "tavily-litellm", "parallel-litellm"])
def test_live_lite_comes_back_short(name: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("what is retrieval augmented generation", max_results=2, search_depth="lite")

    assert response.results and max(len(result.content) for result in response.results) <= 600

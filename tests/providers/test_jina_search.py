"""Jina search provider tests.

``DOCUMENTED`` follows the response Jina documents for ``s.jina.ai`` (a
``data`` list of title, url, description, optional content and date, with
token usage beside them); it is not a recorded reply. Checked live on
2026-11-24: without a key, search answers 401 AuthenticationRequiredError.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.jina import JinaProvider
from web_router.types import SearchRequest

DOCUMENTED: dict[str, Any] = {
    "code": 200,
    "status": 20000,
    "data": [
        {
            "title": "asyncio — Asynchronous I/O",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "description": "asyncio is a library to write concurrent code using the async/await syntax. " * 12,
            "date": "Nov 3, 2026",
            "usage": {"tokens": 1000},
        },
        {
            "title": "Python's asyncio: A Hands-On Walkthrough",
            "url": "https://realpython.com/async-io-python/",
            "description": "In this tutorial, you'll learn how Python asyncio works.",
            "usage": {"tokens": 1000},
        },
    ],
    "meta": {"usage": {"tokens": 10000}},
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> JinaProvider:
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    return JinaProvider()


def _sent(monkeypatch: pytest.MonkeyPatch, request: SearchRequest, body: dict[str, Any] = DOCUMENTED) -> httpx.Request:
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = JinaProvider(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=body)))
    provider.search(request)
    return sent[0]


def test_a_search_is_a_get_with_the_query_and_no_content(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _sent(monkeypatch, SearchRequest(query="python asyncio"))

    assert sent.method == "GET" and sent.url.host == "s.jina.ai" and sent.url.path == "/"
    assert dict(sent.url.params) == {"q": "python asyncio"}
    assert sent.headers["x-respond-with"] == "no-content"
    assert sent.headers["accept"] == "application/json"
    assert sent.headers["authorization"] == "Bearer test-key"


def test_max_results_is_sent_as_num(monkeypatch: pytest.MonkeyPatch) -> None:
    assert dict(_sent(monkeypatch, SearchRequest(query="q", max_results=4)).url.params) == {"q": "q", "num": "4"}


def test_lite_reads_no_page_and_deep_drops_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _sent(monkeypatch, SearchRequest(query="q", search_depth="lite")).headers["x-respond-with"] == "no-content"
    assert "x-respond-with" not in _sent(monkeypatch, SearchRequest(query="q", search_depth="deep")).headers


def test_provider_options_are_sent_as_query_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SearchRequest(query="q", provider_options={"gl": "de", "hl": "de"})
    assert dict(_sent(monkeypatch, request).url.params) == {"q": "q", "gl": "de", "hl": "de"}


def test_search_needs_a_key_although_reader_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="JINA_API_KEY"):
        JinaProvider()


@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"effort": "low"},
        {"search_depth": "fast"},
    ],
)
def test_an_option_jina_search_does_not_map_is_refused(provider: JinaProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="q", **option))


def test_results_map_with_the_date_kept_and_usage_in_extra(provider: JinaProvider) -> None:
    response = provider._to_response(SearchRequest(query="q"), DOCUMENTED)
    first, second = response.results
    description = DOCUMENTED["data"][0]["description"]

    assert response.provider == "jina" and response.raw == DOCUMENTED
    assert first.url == "https://docs.python.org/3/library/asyncio.html"
    assert first.title == "asyncio — Asynchronous I/O"
    assert description.startswith(first.description.removesuffix("\u2026")) and len(first.description) < len(
        description
    )
    assert first.content == description
    assert first.published_date == "Nov 3, 2026" and first.score is None
    assert first.extra == {"usage": {"tokens": 1000}}
    assert second.published_date is None


def test_deep_content_is_the_page_jina_read(provider: JinaProvider) -> None:
    payload = {"data": [{"title": "T", "url": "https://e.com", "description": "d", "content": "# The page"}]}
    (result,) = provider._to_response(SearchRequest(query="q", search_depth="deep"), payload).results
    assert (result.description, result.content) == ("d", "# The page")


@pytest.mark.parametrize("body", [{}, {"code": 422, "message": "bad query"}, {"data": None}])
def test_a_reply_without_a_data_list_is_an_error_not_an_empty_search(
    monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    provider = JinaProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(ProviderError, match="data"):
        provider.search(SearchRequest(query="q"))


@pytest.mark.integration
def test_live_search_returns_results() -> None:
    if not os.environ.get("JINA_API_KEY"):
        pytest.skip("JINA_API_KEY not set")
    response = JinaProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert response.results
    assert all(result.url.startswith("http") for result in response.results)

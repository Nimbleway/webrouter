"""Perplexity provider tests.

The response mapping was verified live on 2026-11-03: every result carries a
URL and text, and ``max_results`` is respected, through the integration test
below and the search checks in tests/test_integration_surface.py.
"""

from __future__ import annotations

import os

import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.perplexity import PerplexityProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> PerplexityProvider:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    return PerplexityProvider()


def test_only_the_query_is_sent_when_nothing_else_is_set(provider: PerplexityProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"query": "hi"}


def test_max_results_is_passed_through(provider: PerplexityProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"query": "hi", "max_results": 5}


def test_the_key_is_sent_as_a_bearer_token(provider: PerplexityProvider) -> None:
    assert provider._client.headers["Authorization"] == "Bearer test-key"


def test_deep_is_refused_until_a_live_call_shows_whole_pages(provider: PerplexityProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="search_depth='deep'"):
        provider.validate(SearchRequest(query="hi", search_depth="deep"))


def test_snippet_becomes_both_description_and_body(provider: PerplexityProvider) -> None:
    """The Search API returns a snippet only, so there is no fuller text to prefer."""
    payload = {
        "id": "req-1",
        "results": [
            {
                "url": "https://example.com",
                "title": "Example",
                "snippet": "a snippet",
                "date": "2026-01-01",
                "last_updated": "2026-02-01",
            }
        ],
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)
    result = response.results[0]

    assert response.request_id == "req-1"
    assert result.description == "a snippet"
    assert result.content == "a snippet"
    assert result.published_date == "2026-01-01"
    assert result.extra["last_updated"] == "2026-02-01"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("perplexity") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "perplexity"
    assert response.results
    assert all(result.url for result in response.results)

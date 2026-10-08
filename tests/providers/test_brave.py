from __future__ import annotations

import os

import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.brave import BraveProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BraveProvider:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    return BraveProvider()


def test_query_maps_onto_braves_q_parameter(provider: BraveProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"q": "hi"}


def test_max_results_maps_onto_count(provider: BraveProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"q": "hi", "count": 5}


def test_depth_is_rejected_because_brave_has_no_content_control(provider: BraveProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="search_depth"):
        provider.validate(SearchRequest(query="hi", search_depth="deep"))


def test_highlight_markup_is_stripped(provider: BraveProvider) -> None:
    """Brave wraps query terms in <strong>; that is its markup, not page content."""
    payload = {
        "web": {
            "results": [
                {
                    "title": "What is <strong>RAG</strong>",
                    "url": "https://example.com",
                    "description": "<strong>RAG</strong> is a technique",
                    "extra_snippets": ["more about <strong>RAG</strong>"],
                    "page_age": "2026-01-01T00:00:00",
                    "age": "4 weeks ago",
                }
            ]
        }
    }

    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]

    assert result.title == "What is RAG"
    assert result.description == "RAG is a technique"
    assert result.content == "RAG is a technique\n\nmore about RAG"
    assert result.published_date == "2026-01-01T00:00:00"
    assert result.extra["age"] == "4 weeks ago"


def test_a_response_with_no_web_section_is_empty_not_an_error(provider: BraveProvider) -> None:
    assert provider._to_response(SearchRequest(query="hi"), {"query": {}}).results == []


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BRAVE_SEARCH_API_KEY"), reason="BRAVE_SEARCH_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("brave") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "brave"
    assert response.results
    assert all(result.url for result in response.results)

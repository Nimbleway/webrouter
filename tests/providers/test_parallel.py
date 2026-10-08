from __future__ import annotations

import os

import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.providers.parallel import ParallelProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ParallelProvider:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    return ParallelProvider()


def test_the_query_becomes_both_objective_and_search_query(provider: ParallelProvider) -> None:
    """Parallel is built around an objective plus the queries to run for it."""
    assert provider._build_payload(SearchRequest(query="hi")) == {
        "objective": "hi",
        "search_queries": ["hi"],
    }


def test_max_results_is_passed_through(provider: ParallelProvider) -> None:
    body = provider._build_payload(SearchRequest(query="hi", max_results=3))
    assert body["max_results"] == 3


def test_excerpts_become_the_body_and_a_snippet_sized_description(provider: ParallelProvider) -> None:
    """Parallel's excerpts run to thousands of characters; a description must not."""
    payload = {
        "search_id": "search_abc",
        "results": [
            {
                "url": "https://example.com",
                "title": "Example",
                "publish_date": "2026-01-01",
                "excerpts": ["x" * 4000, "second excerpt"],
            }
        ],
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)
    result = response.results[0]

    assert response.request_id == "search_abc"
    assert len(result.description) <= SNIPPET_LIMIT + 1  # the ellipsis
    assert result.content == "x" * 4000 + "\n\nsecond excerpt"
    assert result.published_date == "2026-01-01"


def test_a_short_excerpt_is_used_whole(provider: ParallelProvider) -> None:
    payload = {"results": [{"url": "https://a.com", "excerpts": ["a short excerpt"]}]}
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert result.description == "a short excerpt"


def test_missing_excerpts_leave_the_text_empty(provider: ParallelProvider) -> None:
    result = provider._to_response(SearchRequest(query="hi"), {"results": [{"url": "https://a.com"}]}).results[0]
    assert (result.description, result.content) == ("", "")


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PARALLEL_API_KEY"), reason="PARALLEL_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("parallel") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "parallel"
    assert response.results
    assert all(result.url for result in response.results)

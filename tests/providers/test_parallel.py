from __future__ import annotations

import os

import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import UnsupportedParameterError
from web_router.providers.parallel import ParallelProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ParallelProvider:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    return ParallelProvider()


def test_the_query_becomes_both_objective_and_search_query(provider: ParallelProvider) -> None:
    """Parallel is built around an objective plus the queries to run for it; unset options send nothing more."""
    assert provider._build_payload(SearchRequest(query="hi")) == {
        "objective": "hi",
        "search_queries": ["hi"],
        "mode": "fast",
    }


def test_the_v1_endpoint_is_called_with_the_fast_mode_pinned(provider: ParallelProvider) -> None:
    """Unset, /v1 runs `advanced`, the beta's agentic tier: slower and dearer than the beta default it replaces."""
    assert provider.search_path == "/v1/search"
    assert provider._build_payload(SearchRequest(query="hi"))["mode"] == "fast"


def test_max_results_goes_under_advanced_settings(provider: ParallelProvider) -> None:
    """/v1 rejects a top-level max_results; it takes the count under advanced_settings."""
    body = provider._build_payload(SearchRequest(query="hi", max_results=3))
    assert body["advanced_settings"] == {"max_results": 3}
    assert "max_results" not in body


def test_advanced_settings_from_provider_options_merge_with_the_mapped_ones(provider: ParallelProvider) -> None:
    """A caller adding a vendor setting keeps the mapped count, and wins on any field both set."""
    options = {"advanced_settings": {"location": "us", "source_policy": {"after_date": "2026-01-01"}}}
    body = provider._build_payload(SearchRequest(query="hi", max_results=3, provider_options=options))
    assert body["advanced_settings"] == {
        "max_results": 3,
        "location": "us",
        "source_policy": {"after_date": "2026-01-01"},
    }

    overridden = {"advanced_settings": {"max_results": 7}}
    body = provider._build_payload(SearchRequest(query="hi", max_results=3, provider_options=overridden))
    assert body["advanced_settings"] == {"max_results": 7}


def test_provider_options_may_choose_another_mode(provider: ParallelProvider) -> None:
    body = provider._build_payload(SearchRequest(query="hi", provider_options={"mode": "advanced"}))
    assert body["mode"] == "advanced"


def test_options_parallel_does_not_map_are_refused_unsent(provider: ParallelProvider) -> None:
    with pytest.raises(UnsupportedParameterError):
        provider.search(SearchRequest(query="hi", recency="week"))


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

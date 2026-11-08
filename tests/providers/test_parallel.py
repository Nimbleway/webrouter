from __future__ import annotations

import os
from datetime import date

import httpx
import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import UnsupportedParameterError
from web_router.providers._clock import pinned_today
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
        provider.search(SearchRequest(query="hi", locale="en"))


def test_depth_caps_the_excerpts_and_deep_is_refused_unsent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Excerpts are an upper bound chosen for the query, never the page, so no setting serves `deep`."""
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = ParallelProvider(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))

    for depth, chars in (("lite", 500), ("fast", 3000)):
        body = provider._build_payload(SearchRequest(query="hi", search_depth=depth))
        assert body["advanced_settings"] == {"excerpt_settings": {"max_chars_per_result": chars}}
    with pytest.raises(UnsupportedParameterError, match="search_depth='deep'"):
        provider.search(SearchRequest(query="hi", search_depth="deep"))
    assert sent == []


def test_filters_country_and_recency_go_under_advanced_settings(provider: ParallelProvider) -> None:
    request = SearchRequest(query="hi", include_domains=["wikipedia.org"], country="GB", recency="week")
    with pinned_today(date(2026, 11, 9)):
        body = provider._build_payload(request)

    assert body["advanced_settings"] == {
        "source_policy": {"include_domains": ["wikipedia.org"], "after_date": "2026-11-02"},
        "location": "gb",
    }
    excluded = provider._build_payload(SearchRequest(query="hi", exclude_domains=["reddit.com"]))
    assert excluded["advanced_settings"] == {"source_policy": {"exclude_domains": ["reddit.com"]}}


@pytest.mark.parametrize(("recency", "after"), [("day", "2026-11-08"), ("month", "2026-10-10"), ("year", "2025-11-09")])
def test_recency_counts_back_from_today(provider: ParallelProvider, recency: str, after: str) -> None:
    with pinned_today(date(2026, 11, 9)):
        body = provider._build_payload(SearchRequest(query="hi", recency=recency))
    assert body["advanced_settings"] == {"source_policy": {"after_date": after}}


@pytest.mark.parametrize(
    "options",
    [
        {"include_domains": ["a.com"], "exclude_domains": ["b.com"]},
        {"include_domains": [f"d{n}.com" for n in range(201)]},
        {"country": "USA"},
        {"country": "IL"},
        {"effort": "low", "include_domains": ["docs.python.org/3"]},
    ],
)
def test_values_parallel_cannot_serve_are_refused_unsent(options: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parallel ignores exclude beside include and a country off its list; it rejects long lists and turbo paths."""
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = ParallelProvider(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))

    with pytest.raises(UnsupportedParameterError, match=r"Parallel|turbo"):
        provider.search(SearchRequest(query="hi", **options))
    assert sent == []
    assert ParallelProvider.refused_values(SearchRequest(query="hi", **options))


@pytest.mark.parametrize(
    ("effort", "mode"), [("low", "turbo"), ("medium", "fast"), ("high", "advanced"), ("max", "advanced")]
)
def test_effort_picks_the_mode_and_medium_is_the_unset_default(
    provider: ParallelProvider, effort: str, mode: str
) -> None:
    assert provider._build_payload(SearchRequest(query="hi", effort=effort))["mode"] == mode
    assert provider._build_payload(SearchRequest(query="hi", effort="medium")) == provider._build_payload(
        SearchRequest(query="hi")
    )


def test_a_mode_in_provider_options_overrides_effort(provider: ParallelProvider) -> None:
    body = provider._build_payload(SearchRequest(query="hi", effort="low", provider_options={"mode": "basic"}))
    assert body["mode"] == "basic"


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


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PARALLEL_API_KEY"), reason="PARALLEL_API_KEY not set")
def test_live_lite_excerpts_stay_near_their_cap() -> None:
    """max_chars_per_result is an upper bound per URL; lite asks for about a snippet's worth."""
    from web_router import WebRouter

    with WebRouter("parallel") as router:
        response = router.search("retrieval augmented generation", max_results=3, search_depth="lite")

    assert response.results
    assert all(len(result.content) <= 600 for result in response.results)

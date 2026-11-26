"""Nimble search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.types import Recency, SearchDepth, SearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble import NimbleProvider


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleProvider()


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_recency_is_sent_as_time_range(provider: NimbleProvider, recency: Recency) -> None:
    params = provider._build_payload(SearchRequest(query="hi", recency=recency))
    assert params == {"query": "hi", "time_range": recency}


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_recency_is_refused_at_lite_which_does_not_filter_by_it(provider: NimbleProvider, recency: Recency) -> None:
    lite = SearchRequest(query="hi", search_depth="lite", recency=recency)
    assert NimbleProvider.refused_values(lite) == {"recency": recency}
    depths: tuple[SearchDepth | None, ...] = (None, "fast", "deep")
    for depth in depths:
        assert NimbleProvider.refused_values(SearchRequest(query="hi", search_depth=depth, recency=recency)) == {}
    with pytest.raises(UnsupportedParameterError, match="recency"):
        provider.search(lite)


@pytest.mark.parametrize(
    ("depth", "field", "count", "refused"),
    [
        ("fast", "include_domains", 400, False),
        ("fast", "include_domains", 401, True),
        (None, "include_domains", 401, True),
        ("deep", "exclude_domains", 1000, False),
        ("deep", "exclude_domains", 1001, True),
        ("lite", "include_domains", 50, False),
        ("lite", "include_domains", 51, True),
        ("lite", "exclude_domains", 51, True),
        (None, "include_domains", 51, False),
    ],
)
def test_a_domain_list_past_nimbles_limit_is_refused(
    provider: NimbleProvider, depth: SearchDepth | None, field: str, count: int, refused: bool
) -> None:
    """Checked live: one entry past the limit answers 422; with no depth, the account's default depth decides."""
    domains = [f"site-{i}.com" for i in range(count)]
    request = SearchRequest(query="hi", search_depth=depth, **{field: domains})
    assert NimbleProvider.refused_values(request) == ({field: domains} if refused else {})
    if refused:
        with pytest.raises(UnsupportedParameterError, match="at most 400 include_domains"):
            provider.search(request)


def test_lite_counts_distinct_domains() -> None:
    """Nimble checks lite's limit after dropping duplicates."""
    domains = [f"site-{i}.com" for i in range(50)] * 2
    assert NimbleProvider.refused_values(SearchRequest(query="hi", search_depth="lite", include_domains=domains)) == {}


def test_effort_is_refused_unsent_since_nimble_has_no_search_tier(provider: NimbleProvider) -> None:
    assert "effort" not in NimbleProvider.supported_parameters
    with pytest.raises(UnsupportedParameterError, match="effort"):
        provider.search(SearchRequest(query="hi", effort="low"))

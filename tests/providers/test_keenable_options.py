"""Keenable search: how each shared option reaches the API, and what comes back for it.

The search options were checked live on the keyless pool on 2026-11-09:
``snippet_max_length`` 500 returned 281-492 characters and 3,000 returned
2,382-2,932, where unset ran to about 10,600; ``site`` kept every result on
python.org; ``published_after: 1mo`` returned only pages dated in the last
month; and ``mode: realtime`` came back echoed in the response.
"""

from __future__ import annotations

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.keenable import KeenableProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> KeenableProvider:
    monkeypatch.setenv("KEENABLE_API_KEY", "test-key")
    return KeenableProvider()


def test_each_option_becomes_its_keenable_field(provider: KeenableProvider) -> None:
    request = SearchRequest(
        query="hi", search_depth="lite", include_domains=["github.com/microsoft"], recency="month", effort="low"
    )
    assert provider._build_payload(request) == {
        "query": "hi",
        "snippet_max_length": 500,
        "site": "github.com/microsoft",
        "published_after": "1mo",
        "mode": "realtime",
    }
    assert provider._build_payload(SearchRequest(query="hi", search_depth="fast"))["snippet_max_length"] == 3000


@pytest.mark.parametrize(("recency", "delta"), [("day", "1d"), ("week", "7d"), ("month", "1mo"), ("year", "1y")])
def test_recency_is_a_relative_published_after(provider: KeenableProvider, recency: str, delta: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", recency=recency))["published_after"] == delta


@pytest.mark.parametrize(("effort", "mode"), [("low", "realtime"), ("medium", None), ("high", "pro"), ("max", "pro")])
def test_effort_collapses_onto_two_modes_and_medium_sends_nothing(
    provider: KeenableProvider, effort: str, mode: str | None
) -> None:
    """pro is Keenable's default and deeper tier: medium leaves it to the default, high and max name it."""
    assert provider._build_payload(SearchRequest(query="hi", effort=effort)).get("mode") == mode


@pytest.mark.parametrize(
    ("options", "refused"),
    [({"search_depth": "deep"}, "search_depth='deep'"), ({"include_domains": ["a.com", "b.com"]}, "one site")],
)
def test_a_value_keenable_cannot_serve_is_refused_unsent(
    monkeypatch: pytest.MonkeyPatch, options: dict, refused: str
) -> None:
    """Its longest snippet is 10,000 characters, short of a page, and `site` takes a single site."""
    monkeypatch.setenv("KEENABLE_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = KeenableProvider(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))

    with pytest.raises(UnsupportedParameterError, match=refused):
        provider.search(SearchRequest(query="hi", **options))
    assert sent == []


@pytest.mark.integration
def test_live_keyless_options_shape_the_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Free on the keyless pool: a lite snippet stays near its cap, and site keeps to the one site."""
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    provider = KeenableProvider(allow_keyless=True)
    request = SearchRequest(
        query="python asyncio tutorial", max_results=3, search_depth="lite", include_domains=["python.org"]
    )
    try:
        response = provider.search(request)
    finally:
        provider.close()

    assert response.results
    assert all("python.org" in result.url for result in response.results)
    assert all(len(result.content) <= 500 for result in response.results)


@pytest.mark.integration
def test_live_keyless_effort_and_recency_reach_keenable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keenable echoes the mode that served a search, and drops pages with no publish date under a window."""
    monkeypatch.delenv("KEENABLE_API_KEY", raising=False)
    provider = KeenableProvider(allow_keyless=True)
    try:
        fast = provider.search(SearchRequest(query="python asyncio tutorial", max_results=3, effort="low"))
        recent = provider.search(SearchRequest(query="python asyncio tutorial", max_results=3, recency="month"))
    finally:
        provider.close()

    assert fast.raw["mode"] == "realtime"
    assert recent.results and all(result.published_date for result in recent.results)

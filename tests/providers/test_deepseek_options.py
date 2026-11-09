"""DeepSeek search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import os
from datetime import date

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers._clock import pinned_today
from web_router.providers.deepseek import DeepSeekProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> DeepSeekProvider:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    return DeepSeekProvider()


@pytest.mark.parametrize(
    ("recency", "day"), [("day", "2026-11-08"), ("week", "2026-11-02"), ("month", "2026-10-10"), ("year", "2025-11-09")]
)
def test_recency_becomes_an_after_operator_from_the_day_its_window_starts(
    provider: DeepSeekProvider, recency: str, day: str
) -> None:
    with pinned_today(date(2026, 11, 9)):
        payload = provider._build_payload(SearchRequest(query="hi", recency=recency))
    assert payload == {"query": f"hi after:{day}"}


def test_recency_counts_toward_the_query_limit(provider: DeepSeekProvider) -> None:
    """after:YYYY-MM-DD adds 17 characters, so a 112-character query no longer fits."""
    with pytest.raises(ProviderError, match="would be 129"):
        provider._build_payload(SearchRequest(query="q" * 112, recency="week"))


def test_lite_is_the_default_and_deep_asks_for_the_page_body(
    provider: DeepSeekProvider, transport: httpx.MockTransport, sent: list[httpx.Request]
) -> None:
    """Snippet and summary always come back; need_content adds the body. Nothing sits between, so fast is refused."""
    assert provider._build_payload(SearchRequest(query="hi", search_depth="lite")) == {"query": "hi"}
    assert provider._build_payload(SearchRequest(query="hi", search_depth="deep")) == {
        "query": "hi",
        "need_content": True,
    }
    provider = DeepSeekProvider(transport=transport)
    with pytest.raises(UnsupportedParameterError, match=r"search_depth='fast'.*'lite', 'deep'"):
        provider.search(SearchRequest(query="hi", search_depth="fast"))
    assert not sent


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_live_deep_returns_page_bodies_longer_than_lite() -> None:
    """Look at the lengths: deep must carry whole pages, well past lite's summary."""
    from web_router import WebRouter

    with WebRouter("deepseek") as router:
        lite = router.search("what is retrieval augmented generation", max_results=3, search_depth="lite")
        deep = router.search("what is retrieval augmented generation", max_results=3, search_depth="deep")
    assert lite.results and deep.results
    assert max(len(r.content) for r in deep.results) > max(len(r.content) for r in lite.results)


def test_lite_cuts_the_summary_to_snippet_size() -> None:
    from web_router.constants import SNIPPET_LIMIT
    from web_router.providers.deepseek import DeepSeekProvider

    hit = {"url": "https://a.example", "summary": "word " * 1000}
    assert len(DeepSeekProvider._to_result(hit, "lite").content) <= SNIPPET_LIMIT + 3
    assert len(DeepSeekProvider._to_result(hit, "deep").content) > SNIPPET_LIMIT

"""Tavily search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.types import SearchRequest

pytest.importorskip("tavily", reason="tavily extra not installed")

from web_router.providers.tavily import TavilyProvider

BODY = "word " * 300
"""1,500 characters: what ``basic`` can return as three chunks of about 500."""


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> TavilyProvider:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    return TavilyProvider()


@pytest.mark.parametrize(("depth", "cut"), [("lite", True), ("fast", False)])
def test_lite_is_cut_to_a_snippet_and_raw_keeps_the_full_text(provider: TavilyProvider, depth: str, cut: bool) -> None:
    payload = {"results": [{"url": "https://a.com", "content": BODY}]}
    response = provider._to_response(SearchRequest(query="q", search_depth=depth), payload)  # type: ignore[arg-type]
    content = response.results[0].content
    assert (len(content) <= SNIPPET_LIMIT + 1) is cut and (content == BODY) is not cut
    assert response.raw["results"][0]["content"] == BODY

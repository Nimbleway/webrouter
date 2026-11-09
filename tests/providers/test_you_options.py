"""You.com search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.types import SearchRequest

pytest.importorskip("httpx", reason="you extra not installed")

from web_router.providers.you import YouProvider

SNIPPETS = ["word " * 100] * 3
"""Three snippets of 500 characters: what a lite call, with no extraction, can return for one page."""


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> YouProvider:
    monkeypatch.setenv("YOU_API_KEY", "test-key")
    return YouProvider()


@pytest.mark.parametrize(("depth", "cut"), [("lite", True), ("fast", False)])
def test_lite_is_cut_to_a_snippet_and_raw_keeps_the_full_text(provider: YouProvider, depth: str, cut: bool) -> None:
    payload = {"results": {"web": [{"url": "https://a.com", "snippets": SNIPPETS}]}}
    response = provider._to_response(SearchRequest(query="q", search_depth=depth), payload)  # type: ignore[arg-type]
    content = response.results[0].content
    assert (len(content) <= SNIPPET_LIMIT + 1) is cut
    assert response.raw["results"]["web"][0]["snippets"] == SNIPPETS

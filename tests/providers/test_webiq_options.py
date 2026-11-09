"""WebIQ search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.webiq import WebIQProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> WebIQProvider:
    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    return WebIQProvider()


@pytest.mark.parametrize(
    ("depth", "fields"),
    [
        ("lite", {"contentFormat": "passage", "maxLength": 500}),
        ("fast", {"contentFormat": "passage", "maxLength": 3000}),
        ("deep", {"contentFormat": "text", "maxLength": 500_000}),
    ],
)
def test_depth_caps_the_passages_and_deep_asks_for_the_page(provider: WebIQProvider, depth: str, fields: dict) -> None:
    assert provider._build_payload(SearchRequest(query="hi", search_depth=depth)) == {"query": "hi", **fields}


def test_domain_filters_become_site_operators_and_codes_their_fields(provider: WebIQProvider) -> None:
    request = SearchRequest(
        query="coffee", include_domains=["a.com", "b.org"], exclude_domains=["c.net"], country="gb", locale="en-GB"
    )
    assert provider._build_payload(request) == {
        "query": "coffee site:a.com OR site:b.org -site:c.net",
        "contentFormat": "passage",
        "region": "GB",
        "language": "en",
    }


def test_a_query_past_webiqs_limit_with_its_operators_is_refused(provider: WebIQProvider) -> None:
    with pytest.raises(ProviderError, match="at most 1000 characters"):
        provider._build_payload(SearchRequest(query="x" * 990, include_domains=["example.com"]))


@pytest.mark.parametrize(
    "options", [{"country": "USA"}, {"locale": "fil"}, {"country": "U1"}, {"locale": "zz"}, {"locale": "zz-US"}]
)
def test_a_code_webiq_cannot_take_is_refused_unsent(monkeypatch: pytest.MonkeyPatch, options: dict) -> None:
    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = WebIQProvider(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))

    with pytest.raises(UnsupportedParameterError, match="two-letter"):
        provider.search(SearchRequest(query="hi", **options))
    assert sent == []


def test_an_iso_639_1_language_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBIQ_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = WebIQProvider(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))

    provider.search(SearchRequest(query="hi", locale="ja"))
    assert len(sent) == 1
    assert json.loads(sent[0].content)["language"] == "ja"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("WEBIQ_API_KEY"), reason="WEBIQ_API_KEY not set")
def test_live_deep_returns_whole_pages_and_lite_a_snippet() -> None:
    """deep's text format needs full-document access; a passage-only key answers 403 here."""
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("webiq") as router, billing_aware("webiq"):
        lite = router.search("Canberra", max_results=3, search_depth="lite")
        deep = router.search("Canberra", max_results=3, search_depth="deep")

    assert lite.results and all(len(result.content) <= 500 for result in lite.results)
    assert deep.results and max(len(result.content) for result in deep.results) > 10_000

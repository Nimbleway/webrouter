"""You.com search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import UnsupportedParameterError
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


def test_every_shared_option_but_effort_is_mapped(provider: YouProvider) -> None:
    """You.com has no search tier, so effort is the one shared option it refuses."""
    assert provider.supported_parameters == {
        "max_results",
        "search_depth",
        "include_domains",
        "exclude_domains",
        "country",
        "locale",
        "recency",
    }
    with pytest.raises(UnsupportedParameterError, match="effort"):
        provider.validate(SearchRequest(query="hi", effort="low"))


def test_filters_map_onto_you_fields(provider: YouProvider) -> None:
    request = SearchRequest(query="hi", include_domains=["a.com"], country="gb", locale="en-GB", recency="week")
    assert provider._build_payload(request) == {
        "query": "hi",
        "include_domains": ["a.com"],
        "country": "GB",
        "language": "EN-GB",
        "freshness": "week",
    }
    assert provider._build_payload(SearchRequest(query="hi", exclude_domains=["b.com"]))["exclude_domains"] == ["b.com"]


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_every_recency_is_a_freshness_value(provider: YouProvider, recency: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", recency=recency))["freshness"] == recency


@pytest.mark.parametrize(
    ("locale", "language"),
    [
        ("en", "EN"),
        ("en-US", "EN"),
        ("en_gb", "EN-GB"),
        ("pt-BR", "PT-BR"),
        ("pt-pt", "PT-PT"),
        ("zh-CN", "ZH-HANS"),
        ("zh-TW", "ZH-HANT"),
        ("zh-Hant-HK", "ZH-HANT"),
        ("no", "NB"),
        ("fr-CA", "FR"),
    ],
)
def test_a_locale_reaches_the_language_list(provider: YouProvider, locale: str, language: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", locale=locale))["language"] == language


@pytest.mark.parametrize(
    ("option", "value"),
    [("locale", "pt"), ("locale", "zh"), ("locale", "xx"), ("country", "GR"), ("country", "IL")],
)
def test_a_value_off_the_lists_is_refused_unsent(
    provider: YouProvider, transport: httpx.MockTransport, sent: list[httpx.Request], option: str, value: str
) -> None:
    """Upper-casing pt or zh sends a value You.com answers 422 for, and GR is not among its 36 countries."""
    provider = YouProvider(transport=transport)
    with pytest.raises(UnsupportedParameterError, match=rf"{option}='{value}'") as caught:
        provider.search(SearchRequest(query="hi", **{option: value}))
    assert caught.value.values == {option: value}
    assert YouProvider.refused_values(SearchRequest(query="hi", **{option: value})) == {option: value}
    assert not sent


def test_both_domain_lists_are_refused_unsent(
    provider: YouProvider, transport: httpx.MockTransport, sent: list[httpx.Request]
) -> None:
    provider = YouProvider(transport=transport)
    with pytest.raises(UnsupportedParameterError, match="one domain list"):
        provider.search(SearchRequest(query="hi", include_domains=["a.com"], exclude_domains=["b.com"]))
    assert not sent


needs_key = pytest.mark.skipif(not os.getenv("YOU_API_KEY"), reason="YOU_API_KEY not set")


@pytest.mark.integration
@needs_key
def test_live_a_regional_locale_and_country_are_accepted() -> None:
    """pt-BR must reach You.com as PT-BR, a value it lists, and come back with results."""
    from web_router import WebRouter

    with WebRouter("you") as router:
        response = router.search("futebol noticias", max_results=3, locale="pt-BR", country="BR")
    assert response.results

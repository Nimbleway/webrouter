from __future__ import annotations

import os

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.types import SearchRequest

pytest.importorskip("httpx", reason="you extra not installed")

from web_router.providers.you import YouProvider


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> YouProvider:
    monkeypatch.setenv("YOU_API_KEY", "test-key")
    return YouProvider()


def test_only_the_query_is_sent_when_nothing_else_is_set(provider: YouProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"query": "hi"}


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


def test_max_results_maps_onto_the_you_count_field(provider: YouProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"query": "hi", "count": 5}


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


def test_provider_options_reach_the_api_untranslated(provider: YouProvider) -> None:
    """The escape hatch for anything You.com supports that we do not map."""
    body = provider._build_payload(SearchRequest(query="hi", provider_options={"safesearch": "off"}))
    assert body["safesearch"] == "off"


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        ("lite", None),
        ("fast", {"extraction_mode": "highlights"}),
        ("deep", {"extraction_mode": "full_page"}),
    ],
)
def test_depth_maps_onto_the_extraction_object(provider: YouProvider, depth: str, expected: dict | None) -> None:
    body = provider._build_payload(SearchRequest(query="hi", search_depth=depth))
    assert body.get("extraction") == expected


def test_web_and_news_sections_are_flattened_in_order(provider: YouProvider) -> None:
    payload = {
        "metadata": {"search_uuid": "uuid-1"},
        "results": {
            "web": [
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "description": "A site",
                    "snippets": ["one", "two"],
                    "page_age": "2026-01-01",
                    "favicon_url": "https://example.com/f.ico",
                }
            ],
            "news": [{"url": "https://news.com", "title": "News"}],
            "knowledge": [{"type": "answer", "description": "42"}],
        },
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)

    assert response.request_id == "uuid-1"
    assert response.answer == "42"
    assert response.urls == ["https://example.com", "https://news.com"]

    web, news = response.results
    assert web.content == "one\n\ntwo"
    assert web.published_date == "2026-01-01"
    assert web.extra["section"] == "web"
    assert web.extra["favicon_url"] == "https://example.com/f.ico"
    assert news.extra["section"] == "news"


def test_extracted_markdown_wins_over_snippets(provider: YouProvider) -> None:
    payload = {
        "results": {
            "web": [
                {
                    "url": "https://a.com",
                    "snippets": ["snippet"],
                    "contents": {"markdown": "the whole page"},
                }
            ]
        }
    }
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert result.content == "the whole page"


def test_description_is_the_body_when_no_snippets_came_back(provider: YouProvider) -> None:
    payload = {"results": {"web": [{"url": "https://a.com", "description": "just a summary"}]}}
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert result.description == "just a summary"
    assert result.content == "just a summary"


def test_missing_knowledge_answer_stays_none(provider: YouProvider) -> None:
    payload = {"results": {"web": [{"url": "https://a.com"}], "knowledge": [{"type": "entity"}]}}
    assert provider._to_response(SearchRequest(query="hi"), payload).answer is None


needs_key = pytest.mark.skipif(not os.getenv("YOU_API_KEY"), reason="YOU_API_KEY not set")


@pytest.mark.integration
@needs_key
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("you") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "you"
    assert response.results
    assert all(result.url for result in response.results)


@pytest.mark.integration
@needs_key
def test_live_a_regional_locale_and_country_are_accepted() -> None:
    """pt-BR must reach You.com as PT-BR, a value it lists, and come back with results."""
    from web_router import WebRouter

    with WebRouter("you") as router:
        response = router.search("futebol noticias", max_results=3, locale="pt-BR", country="BR")
    assert response.results

from __future__ import annotations

import os

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


def test_the_minimal_surface_is_query_max_results_and_depth(provider: YouProvider) -> None:
    assert provider.supported_parameters == {"max_results", "search_depth"}


def test_max_results_maps_onto_the_you_count_field(provider: YouProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"query": "hi", "count": 5}


@pytest.mark.parametrize("parameter", ["include_domains", "exclude_domains", "country", "locale"])
def test_unmapped_parameters_are_rejected_not_dropped(provider: YouProvider, parameter: str) -> None:
    """You.com is called with the minimum. Anything else must fail loudly, never silently."""
    values = {"include_domains": ["a.com"], "exclude_domains": ["a.com"], "country": "US", "locale": "en"}
    request = SearchRequest(query="hi", **{parameter: values.get(parameter, True)})
    with pytest.raises(UnsupportedParameterError, match=parameter):
        provider.validate(request)


def test_the_rejection_points_at_the_way_forward(provider: YouProvider) -> None:
    """The message is the contribution prompt, so it must name both routes."""
    with pytest.raises(UnsupportedParameterError, match=r"provider_options.*CONTRIBUTING"):
        provider.validate(SearchRequest(query="hi", country="US"))


def test_provider_options_reach_the_api_untranslated(provider: YouProvider) -> None:
    """The escape hatch for anything You.com supports that we do not map."""
    body = provider._build_payload(SearchRequest(query="hi", provider_options={"country": "FR"}))
    assert body["country"] == "FR"


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


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("YOU_API_KEY"), reason="YOU_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("you") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "you"
    assert response.results
    assert all(result.url for result in response.results)

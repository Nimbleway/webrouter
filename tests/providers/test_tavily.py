from __future__ import annotations

import os

import pytest

from web_router.exceptions import UnsupportedFormatError, UnsupportedParameterError
from web_router.types import ExtractFailure, ExtractRequest, SearchRequest

pytest.importorskip("tavily", reason="tavily extra not installed")

from web_router.providers.tavily import TavilyExtractProvider, TavilyProvider

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> TavilyProvider:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    return TavilyProvider()


def test_only_the_query_is_sent_when_nothing_else_is_set(provider: TavilyProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"query": "hi"}


def test_the_minimal_surface_is_query_max_results_and_depth(provider: TavilyProvider) -> None:
    assert provider.supported_parameters == {"max_results", "search_depth"}


def test_max_results_is_passed_through(provider: TavilyProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"query": "hi", "max_results": 5}


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        ("lite", {"search_depth": "basic"}),
        ("fast", {"search_depth": "advanced"}),
        ("deep", {"search_depth": "advanced", "include_raw_content": "markdown"}),
    ],
)
def test_depth_maps_onto_tavilys_two_knobs(provider: TavilyProvider, depth: str, expected: dict) -> None:
    params = provider._build_payload(SearchRequest(query="hi", search_depth=depth))
    assert params == {"query": "hi", **expected}


@pytest.mark.parametrize("parameter", ["include_domains", "exclude_domains", "country", "locale"])
def test_unmapped_parameters_are_rejected_not_dropped(provider: TavilyProvider, parameter: str) -> None:
    """Tavily is called with the minimum. Anything else must fail loudly, never silently."""
    values = {"include_domains": ["a.com"], "exclude_domains": ["a.com"], "country": "US", "locale": "en"}
    request = SearchRequest(query="hi", **{parameter: values.get(parameter, True)})
    with pytest.raises(UnsupportedParameterError, match=parameter):
        provider.validate(request)


def test_the_rejection_points_at_the_way_forward(provider: TavilyProvider) -> None:
    """The message is the contribution prompt, so it must name both routes."""
    with pytest.raises(UnsupportedParameterError, match=r"provider_options.*CONTRIBUTING"):
        provider.validate(SearchRequest(query="hi", country="US"))


def test_provider_options_reach_the_sdk_untranslated(provider: TavilyProvider) -> None:
    """The escape hatch for anything Tavily supports that we do not map."""
    params = provider._build_payload(SearchRequest(query="hi", provider_options={"country": "france"}))
    assert params["country"] == "france"


def test_raw_content_becomes_the_body_and_the_snippet_the_description(provider: TavilyProvider) -> None:
    payload = {
        "query": "hi",
        "answer": "42",
        "request_id": "req-1",
        "results": [
            {
                "title": "Example",
                "url": "https://example.com",
                "content": "a snippet",
                "raw_content": "the whole page",
                "score": 0.93,
                "published_date": "2026-01-01",
                "favicon": "https://example.com/f.ico",
            }
        ],
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)
    result = response.results[0]

    assert response.provider == "tavily"
    assert response.answer == "42"
    assert response.request_id == "req-1"
    assert result.description == "a snippet"
    assert result.content == "the whole page"
    assert result.score == 0.93
    assert result.published_date == "2026-01-01"
    assert result.extra == {"favicon": "https://example.com/f.ico"}


def test_snippet_is_the_body_when_no_raw_content_was_requested(provider: TavilyProvider) -> None:
    payload = {"results": [{"url": "https://a.com", "content": "a snippet"}]}
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert result.description == "a snippet"
    assert result.content == "a snippet"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("tavily") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "tavily"
    assert response.results
    assert all(result.url for result in response.results)


# -- Extract -----------------------------------------------------------------


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> TavilyExtractProvider:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    return TavilyExtractProvider()


def test_every_url_goes_in_one_call(extractor: TavilyExtractProvider) -> None:
    urls = [URL, "https://example.org"]
    assert extractor.batches_urls
    assert extractor._build_payload(ExtractRequest(urls=urls), urls) == {"urls": urls, "format": "markdown"}


def test_markdown_is_named_explicitly_even_though_it_is_tavilys_default(
    extractor: TavilyExtractProvider,
) -> None:
    """So `ExtractedDocument.format` is true because it was asked for, not because a default held."""
    payload = extractor._build_payload(ExtractRequest(urls=[URL]), [URL])
    assert payload["format"] == "markdown"


def test_plain_text_maps_onto_tavilys_own_text_format(extractor: TavilyExtractProvider) -> None:
    payload = extractor._build_payload(ExtractRequest(urls=[URL], format="text"), [URL])
    assert payload["format"] == "text"


def test_html_is_rejected_because_tavily_returns_a_422_for_it(extractor: TavilyExtractProvider) -> None:
    """Verified live: Tavily's extract endpoint rejects format="html"."""
    with pytest.raises(UnsupportedFormatError, match="html"):
        extractor.validate(ExtractRequest(urls=[URL], format="html"))


def test_tavily_extract_payload_is_normalized(extractor: TavilyExtractProvider) -> None:
    payload = {
        "results": [
            {"url": URL, "title": "Example Domain", "raw_content": "# Example", "images": []},
        ],
        "failed_results": [{"url": "https://broken.example", "error": "Failed to fetch url"}],
        "response_time": 0.23,
        "request_id": "abc-123",
    }

    response = extractor._to_response(ExtractRequest(urls=[URL], format="markdown"), [URL], payload)

    assert response.provider == "tavily"
    assert response.request_id == "abc-123"

    document = response.documents[0]
    assert document.url == URL
    assert document.title == "Example Domain"
    assert document.content == "# Example"
    assert document.format == "markdown"
    assert document.extra == {"images": []}

    assert response.failures == [
        ExtractFailure(url="https://broken.example", error="Failed to fetch url"),
    ]
    assert response.raw == [payload]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
def test_live_extract_returns_page_content() -> None:
    from web_router import WebRouter

    with WebRouter("tavily") as router:
        response = router.extract(URL)

    assert response.provider == "tavily"
    assert response.documents[0].content


@pytest.mark.integration
def test_live_keyless_extract_answers_without_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tavily's anonymous tier is what `auto` falls back to, so it has to keep working.

    No skipif: with the key removed there is nothing to skip on, which is the
    point of the test.
    """
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    provider = TavilyExtractProvider(allow_keyless=True)
    try:
        response = provider.extract(ExtractRequest(urls=[URL]))
    finally:
        provider.close()

    assert response.documents[0].content

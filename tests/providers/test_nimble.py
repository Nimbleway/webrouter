from __future__ import annotations

import os

import pytest

from web_router.exceptions import UnsupportedFormatError
from web_router.types import ExtractFormat, ExtractRequest, SearchDepth, SearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble import NimbleExtractProvider, NimbleProvider

URL = "https://example.com"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleProvider()


def test_only_the_query_is_sent_when_nothing_else_is_set(provider: NimbleProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"query": "hi"}


def test_set_fields_map_onto_nimble_parameters(provider: NimbleProvider) -> None:
    params = provider._build_payload(
        SearchRequest(
            query="hi",
            max_results=5,
            search_depth="deep",
            include_domains=["a.com"],
            country="US",
        )
    )
    assert params == {
        "query": "hi",
        "max_results": 5,
        "search_depth": "standard",
        "full_content": True,
        "include_domains": ["a.com"],
        "country": "US",
    }


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        ("lite", {"search_depth": "lite"}),
        ("fast", {"search_depth": "standard"}),
        ("deep", {"search_depth": "standard", "full_content": True}),
    ],
)
def test_depth_maps_onto_nimbles_own_vocabulary(
    provider: NimbleProvider, depth: SearchDepth, expected: dict[str, object]
) -> None:
    """Nimble accepts only lite/standard; "fast" and "deep" are ours, not its."""
    params = provider._build_payload(SearchRequest(query="hi", search_depth=depth))
    assert params == {"query": "hi", **expected}


def test_depth_is_absent_when_the_caller_did_not_ask_for_one(provider: NimbleProvider) -> None:
    assert "search_depth" not in provider._build_payload(SearchRequest(query="hi"))


def test_provider_options_pass_through(provider: NimbleProvider) -> None:
    params = provider._build_payload(SearchRequest(query="hi", provider_options={"focus": "news"}))
    assert params["focus"] == "news"


def test_nimble_payload_is_normalized(provider: NimbleProvider) -> None:
    payload = {
        "request_id": "abc-123",
        "total_results": 1,
        "answer": "42",
        "results": [
            {
                "url": "https://example.com",
                "title": "Example",
                "description": "A site",
                "content": "body text",
                "metadata": {"position": 1, "entity_type": "organic"},
                "additional_data": {"publish_date": "2026-01-01", "price": "$10"},
            }
        ],
    }

    response = provider._to_response(SearchRequest(query="hi"), payload)

    assert response.provider == "nimble"
    assert response.request_id == "abc-123"
    assert response.answer == "42"
    assert len(response) == 1

    result = response.results[0]
    assert result.url == "https://example.com"
    assert result.content == "body text"
    assert result.published_date == "2026-01-01"
    assert result.extra["price"] == "$10"
    assert result.extra["metadata"]["position"] == 1
    assert response.raw == payload


def test_missing_optional_fields_become_empty_strings(provider: NimbleProvider) -> None:
    response = provider._to_response(SearchRequest(query="hi"), {"results": [{"url": "https://a.com"}]})
    result = response.results[0]
    assert (result.title, result.description, result.content) == ("", "", "")
    assert result.extra == {"metadata": {}}


def test_enriched_description_becomes_the_body_when_content_is_empty(provider: NimbleProvider) -> None:
    """At mid depths Nimble returns the page body in `description` and leaves `content` empty."""
    payload = {"results": [{"url": "https://a.com", "description": "a long enriched body", "content": ""}]}
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert result.content == "a long enriched body"


def test_a_real_content_field_is_preferred_over_the_description(provider: NimbleProvider) -> None:
    payload = {"results": [{"url": "https://a.com", "description": "a snippet", "content": "the whole page"}]}
    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]
    assert result.description == "a snippet"
    assert result.content == "the whole page"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("nimble") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "nimble"
    assert response.results
    assert all(result.url for result in response.results)


# -- Extract -----------------------------------------------------------------


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> NimbleExtractProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleExtractProvider()


def test_one_url_and_one_format_are_sent(extractor: NimbleExtractProvider) -> None:
    """Nimble's realtime extract takes a single URL, so the base class fans out -- in markdown unless told otherwise."""
    assert extractor._build_payload(ExtractRequest(urls=[URL]), [URL]) == {"url": URL, "formats": ["markdown"]}
    assert not extractor.batches_urls


@pytest.mark.parametrize(("body_format", "expected"), [("markdown", "markdown"), ("html", "html")])
def test_format_maps_onto_nimbles_formats_list(
    extractor: NimbleExtractProvider, body_format: ExtractFormat, expected: str
) -> None:
    payload = extractor._build_payload(ExtractRequest(urls=[URL], format=body_format), [URL])
    assert payload["formats"] == [expected]


def test_plain_text_is_rejected_because_nimble_does_not_return_it(extractor: NimbleExtractProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        extractor.validate(ExtractRequest(urls=[URL], format="text"))


def test_nimble_extract_payload_is_normalized(extractor: NimbleExtractProvider) -> None:
    payload = {
        "task_id": "abc-123",
        "url": "https://example.com/",
        "status": "success",
        "status_code": 200.0,
        "data": {"markdown": "# Example", "html": None, "links": ["https://iana.org"]},
        "metadata": {"driver": "vx6"},
    }

    response = extractor._to_response(ExtractRequest(urls=[URL], format="markdown"), [URL], payload)

    assert response.provider == "nimble"
    assert response.request_id == "abc-123"
    assert len(response) == 1

    document = response.documents[0]
    assert document.url == "https://example.com/"
    assert document.content == "# Example"
    assert document.format == "markdown"
    assert document.status_code == 200, "Nimble sends its status code as a float"
    assert document.extra["metadata"] == {"driver": "vx6"}
    assert document.extra["links"] == ["https://iana.org"]
    assert response.raw == [payload]


def test_a_status_other_than_success_is_a_failure_not_an_empty_document(
    extractor: NimbleExtractProvider,
) -> None:
    """Nimble answers 200 and refuses in `status`, which would otherwise look like a blank page."""
    payload = {"url": URL, "status": "blocked", "data": {}, "warnings": ["challenge not solved"]}

    response = extractor._to_response(ExtractRequest(urls=[URL]), [URL], payload)

    assert not response.documents
    assert response.failures[0].url == URL
    assert "blocked" in response.failures[0].error
    assert "challenge not solved" in response.failures[0].error


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_extract_returns_page_content() -> None:
    from web_router import WebRouter

    with WebRouter("nimble") as router:
        response = router.extract(URL, format="markdown")

    assert response.provider == "nimble"
    assert response.documents[0].content
    assert response.documents[0].format == "markdown"

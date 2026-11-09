"""Linkup search and extract provider tests, over a stubbed transport.

Verified against the live API on 2026-09-30: the search request and response
shape at every depth, `maxResults`, `includeDomains` and `excludeDomains`, the
400 that a missing `depth` earns, and `/fetch` with and without
`includeRawContent`, on an HTML page, a PDF and a JSON endpoint. The payloads
below are trimmed from those calls.

Not verified live, and documentation-derived: the shape of an image result
(`name`, `url` and `type` alone) -- `includeImages` never returned one, on the
queries tried -- and every option left unmapped: `toDate`,
`includeImages`, `sourcedAnswer` and `structured` output, and `/fetch`'s `mode`,
`renderJs`, `extractImages` and `schema`. All of them reach the API through
`provider_options`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from web_router import WebRouter
from web_router.constants import LINKUP_DEFAULT_DEPTH
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.linkup import LinkupExtractProvider, LinkupProvider
from web_router.types import ExtractRequest, SearchDepth, SearchRequest

URL = "https://example.com"

SEARCH_PAYLOAD = {
    "results": [
        {
            "name": "Async IO in Python",
            "url": "https://example.com/asyncio",
            "content": "asyncio is a library to write concurrent code using the async/await syntax.",
            "favicon": "https://favicons.linkup.so?domain=example.com",
            "type": "text",
        },
        {"name": "A diagram", "url": "https://example.com/diagram.png", "type": "image"},
    ]
}

FETCH_MARKDOWN = {
    "favicon": "https://favicons.linkup.so?domain=example.com",
    "images": [],
    "markdown": "This domain is for use in documentation examples.",
}

FETCH_HTML = {
    **FETCH_MARKDOWN,
    "contentType": "html",
    "rawContent": "<!doctype html><html lang=en><head></head><body>This domain is for use.</body></html>",
}


def _stub(provider: Any, answer: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
    """Answer this provider's HTTP calls from `answer`, through its real client settings."""
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer(request)

    transport = httpx.MockTransport(handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return sent


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> LinkupProvider:
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    return LinkupProvider()


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> LinkupExtractProvider:
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    return LinkupExtractProvider()


# -- Search: what gets sent ---------------------------------------------------


def test_a_bare_search_sends_the_two_fields_linkup_requires_beside_the_query(provider: LinkupProvider) -> None:
    """`depth` has no server default -- a request without it is a 400 -- so it is filled in."""
    assert provider._build_payload(SearchRequest(query="hi")) == {
        "q": "hi",
        "depth": LINKUP_DEFAULT_DEPTH,
        "outputType": "searchResults",
    }


def test_a_search_never_asks_linkup_for_an_answer_it_wrote(provider: LinkupProvider) -> None:
    payload = provider._build_payload(SearchRequest(query="hi", max_results=3))
    assert payload["outputType"] == "searchResults"


def test_options_map_onto_linkups_field_names(provider: LinkupProvider) -> None:
    payload = provider._build_payload(
        SearchRequest(query="hi", max_results=5, include_domains=["a.com"], exclude_domains=["b.com"])
    )
    assert payload["maxResults"] == 5
    assert payload["includeDomains"] == ["a.com"]
    assert payload["excludeDomains"] == ["b.com"]


@pytest.mark.parametrize("depth", ["lite", "fast", "deep"])
def test_search_depth_raises_because_linkups_depth_is_effort_not_content(
    provider: LinkupProvider, depth: SearchDepth
) -> None:
    with pytest.raises(UnsupportedParameterError, match="search_depth"):
        provider.search(SearchRequest(query="hi", search_depth=depth))


def test_provider_options_reach_the_api_and_choose_linkups_own_depth(provider: LinkupProvider) -> None:
    payload = provider._build_payload(
        SearchRequest(query="hi", provider_options={"depth": "deep", "fromDate": "2026-01-01"})
    )
    assert payload["depth"] == "deep"
    assert payload["fromDate"] == "2026-01-01"


@pytest.mark.parametrize("option", [{"country": "US"}, {"locale": "en"}])
def test_an_option_linkup_has_no_field_for_raises_rather_than_being_dropped(
    provider: LinkupProvider, option: dict[str, str]
) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="hi", **option))


def test_the_key_is_sent_as_a_bearer_token_and_the_body_is_json(provider: LinkupProvider) -> None:
    sent = _stub(provider, lambda request: httpx.Response(200, json=SEARCH_PAYLOAD))

    provider.search(SearchRequest(query="hi", max_results=2))

    assert sent[0].url.path == "/v1/search"
    assert sent[0].headers["Authorization"] == "Bearer test-key"
    assert json.loads(sent[0].content) == {"q": "hi", "depth": "fast", "outputType": "searchResults", "maxResults": 2}


# -- Search: what comes back --------------------------------------------------


def test_results_are_normalized_without_inventing_a_score_or_a_date(provider: LinkupProvider) -> None:
    response = provider._to_response(SearchRequest(query="hi"), SEARCH_PAYLOAD)
    result = response.results[0]

    assert response.provider == "linkup"
    assert result.url == "https://example.com/asyncio"
    assert result.title == "Async IO in Python"
    assert result.content.startswith("asyncio is a library")
    assert result.description == result.content
    assert result.score is None
    assert result.published_date is None
    assert result.extra == {"favicon": "https://favicons.linkup.so?domain=example.com", "type": "text"}
    assert response.raw == SEARCH_PAYLOAD


def test_an_image_result_is_kept_with_no_text_rather_than_dropped_or_given_some(provider: LinkupProvider) -> None:
    """Images arrive only when a caller opts in, and carry no content to fake."""
    response = provider._to_response(SearchRequest(query="hi"), SEARCH_PAYLOAD)
    image = response.results[1]

    assert image.url == "https://example.com/diagram.png"
    assert image.title == "A diagram"
    assert image.content == ""
    assert image.description == ""
    assert image.extra == {"type": "image"}


def test_a_response_with_no_results_is_an_empty_list(provider: LinkupProvider) -> None:
    assert provider._to_response(SearchRequest(query="hi"), {"results": []}).results == []


async def test_the_async_path_sends_the_same_request(provider: LinkupProvider) -> None:
    sent = _stub(provider, lambda request: httpx.Response(200, json=SEARCH_PAYLOAD))

    response = await provider.asearch(SearchRequest(query="hi"))

    assert len(response.results) == 2
    assert json.loads(sent[0].content)["depth"] == "fast"


def test_a_400_names_what_linkup_rejected(provider: LinkupProvider) -> None:
    body = {"error": {"code": "VALIDATION_ERROR", "message": "Validation failed"}, "statusCode": 400}
    _stub(provider, lambda request: httpx.Response(400, json=body))

    with pytest.raises(ProviderError, match="VALIDATION_ERROR"):
        provider.search(SearchRequest(query="hi"))


# -- Extract: what gets sent --------------------------------------------------


def test_markdown_sends_the_url_alone_because_fetch_always_returns_it(extractor: LinkupExtractProvider) -> None:
    assert extractor._build_payload(ExtractRequest(urls=[URL]), [URL]) == {"url": URL}
    assert not extractor.batches_urls


def test_html_asks_for_the_raw_content(extractor: LinkupExtractProvider) -> None:
    payload = extractor._build_payload(ExtractRequest(urls=[URL], format="html"), [URL])
    assert payload == {"url": URL, "includeRawContent": True}


def test_plain_text_is_rejected_because_linkup_cannot_return_it(extractor: LinkupExtractProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        extractor.validate(ExtractRequest(urls=[URL], format="text"))


def test_fetch_is_called_on_its_own_path_with_the_key(extractor: LinkupExtractProvider) -> None:
    sent = _stub(extractor, lambda request: httpx.Response(200, json=FETCH_MARKDOWN))

    extractor.extract(ExtractRequest(urls=[URL]))

    assert sent[0].url.path == "/v1/fetch"
    assert sent[0].headers["Authorization"] == "Bearer test-key"


# -- Extract: what comes back -------------------------------------------------


def test_markdown_is_the_default_and_is_labelled_as_such(extractor: LinkupExtractProvider) -> None:
    response = extractor._to_response(ExtractRequest(urls=[URL]), [URL], FETCH_MARKDOWN)
    document = response.documents[0]

    assert document.url == URL
    assert document.title == "", "Linkup returns no title, so none is made up"
    assert document.content == FETCH_MARKDOWN["markdown"]
    assert document.format == "markdown"
    assert document.extra["favicon"] == FETCH_MARKDOWN["favicon"]
    assert response.raw == [FETCH_MARKDOWN]


def test_html_content_is_the_raw_content_not_the_markdown(extractor: LinkupExtractProvider) -> None:
    response = extractor._to_response(ExtractRequest(urls=[URL], format="html"), [URL], FETCH_HTML)
    document = response.documents[0]

    assert document.format == "html"
    assert document.content == FETCH_HTML["rawContent"]
    assert document.extra["contentType"] == "html"
    assert response.raw == [FETCH_HTML], "the markdown Linkup also sent stays recoverable"


def test_html_for_a_page_with_no_raw_content_is_a_failure_not_markdown_under_html_s_name(
    extractor: LinkupExtractProvider,
) -> None:
    """A PDF came back from the live API with markdown and neither `rawContent` nor `contentType`."""
    pdf = {"favicon": "https://favicons.linkup.so?domain=arxiv.org", "images": [], "markdown": "Attention is all"}

    response = extractor._to_response(ExtractRequest(urls=[URL], format="html"), [URL], pdf)

    assert not response.documents
    assert response.failures[0].url == URL
    assert "no HTML" in response.failures[0].error


def test_an_unfetchable_url_is_a_failure_and_a_lone_one_raises_with_the_reason(
    extractor: LinkupExtractProvider,
) -> None:
    body = {"error": {"code": "FETCH_TARGET_UNREACHABLE", "message": "The target URL could not be reached"}}
    _stub(extractor, lambda request: httpx.Response(400, json=body))

    with pytest.raises(ProviderError, match="FETCH_TARGET_UNREACHABLE"):
        extractor.extract(ExtractRequest(urls=[URL]))


def test_one_bad_url_does_not_sink_the_others(extractor: LinkupExtractProvider) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["url"] == "https://broken.example":
            return httpx.Response(400, json={"error": {"code": "FETCH_TARGET_UNREACHABLE"}})
        return httpx.Response(200, json=FETCH_MARKDOWN)

    _stub(extractor, answer)

    response = extractor.extract(ExtractRequest(urls=[URL, "https://broken.example"]))

    assert response.urls == [URL]
    assert [failure.url for failure in response.failures] == ["https://broken.example"]


# -- Live ---------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("LINKUP_API_KEY"), reason="LINKUP_API_KEY not set")
def test_live_search_returns_results() -> None:
    with WebRouter("linkup") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "linkup"
    assert 0 < len(response.results) <= 3
    assert all(result.url and result.content for result in response.results)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("LINKUP_API_KEY"), reason="LINKUP_API_KEY not set")
def test_live_extract_returns_markdown_and_html() -> None:
    with WebRouter("linkup") as router:
        markdown = router.extract(URL).documents[0]
        html = router.extract(URL, format="html").documents[0]

    assert markdown.format == "markdown"
    assert markdown.content
    assert "<html" not in markdown.content.lower()
    assert html.format == "html"
    assert "<html" in html.content.lower()

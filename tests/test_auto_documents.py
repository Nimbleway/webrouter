"""Extract ``auto``'s document step: file URLs go to the document parser first, everything else down the chain."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider
from web_router.exceptions import ProviderError, WebRouterError
from web_router.providers._auto_documents import is_document
from web_router.providers.auto import AutoExtractProvider
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractedDocument, ExtractFormat, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_TARGET

PDF = "https://example.com/report.pdf"
PAGE = "https://example.com/about"


MISSING = "https://example.com/gone.pdf"
BLANK = "https://example.com/scan.pdf"


class FakeParser(FakeExtractProvider):
    name = "fake-parser"
    supported_parameters = frozenset({"format"})
    supported_formats: frozenset[ExtractFormat] = frozenset({"markdown", "text"})
    content: str | None = "parsed"

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        self.calls.append(list(urls))
        if urls[0] == MISSING:
            raise ProviderError(self.name, "HTTP 404", kind=ERROR_KIND_TARGET)
        if self.content is None:
            raise ProviderError(self.name, "not a document")
        if urls[0] == BLANK:
            return ExtractResponse(provider=self.name, documents=[ExtractedDocument(url=urls[0], content="")])
        document = ExtractedDocument(url=urls[0], content=self.content, format=self.format_of(request))
        return ExtractResponse(provider=self.name, documents=[document])


@pytest.fixture
def router(fake_extract_provider: Any) -> Iterator[AutoExtractProvider]:
    register_extract_provider("fake-parser", FakeParser)
    auto = AutoExtractProvider(chain=(("fake", {}),), document_parser="fake-parser")
    yield auto
    auto.close()
    unregister_extract_provider("fake-parser")


def _calls(auto: AutoExtractProvider) -> tuple[list[list[str]], list[list[str]]]:
    parser = auto._parser
    return (parser.calls if isinstance(parser, FakeParser) else []), auto._member(0).calls  # type: ignore[attr-defined]


def test_a_document_url_is_parsed_and_a_page_goes_down_the_chain(router: AutoExtractProvider) -> None:
    response = router.extract(ExtractRequest(urls=[PDF, PAGE]))

    assert [(d.url, d.content) for d in response.documents] == [(PDF, "parsed"), (PAGE, "hello")]
    assert response.provider == "fake-parser"
    assert _calls(router) == ([[PDF]], [[PAGE]])


def test_the_async_path_routes_the_same_way(router: AutoExtractProvider) -> None:
    response = asyncio.run(router.aextract(ExtractRequest(urls=[PDF, PAGE])))

    assert [(d.url, d.content) for d in response.documents] == [(PDF, "parsed"), (PAGE, "hello")]
    assert _calls(router) == ([[PDF]], [[PAGE]])


def test_a_document_the_parser_cannot_read_falls_back_to_the_chain(router: AutoExtractProvider) -> None:
    FakeParser.content = None
    try:
        response = router.extract(ExtractRequest(urls=[PDF]))
    finally:
        FakeParser.content = "parsed"

    assert [(d.url, d.content) for d in response.documents] == [(PDF, "hello")]
    assert response.provider == "fake"
    assert _calls(router) == ([[PDF]], [[PDF]])


def test_a_blank_parse_falls_back_to_the_chain(router: AutoExtractProvider) -> None:
    FakeParser.content = "  "
    try:
        response = router.extract(ExtractRequest(urls=[PDF]))
    finally:
        FakeParser.content = "parsed"

    assert [(d.url, d.content) for d in response.documents] == [(PDF, "hello")]
    assert _calls(router) == ([[PDF]], [[PDF]])


def test_html_skips_the_parser_rather_than_relabelling_its_output(router: AutoExtractProvider) -> None:
    response = router.extract(ExtractRequest(urls=[PDF], format="html"))

    assert response.provider == "fake"
    assert _calls(router) == ([], [[PDF]])


def test_no_document_parser_sends_every_url_down_the_chain(fake_extract_provider: Any) -> None:
    auto = AutoExtractProvider(chain=(("fake", {}),), document_parser=None)
    try:
        response = auto.extract(ExtractRequest(urls=[PDF]))
    finally:
        auto.close()

    assert response.provider == "fake"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a.PDF", True),
        ("https://example.com/deck.pptx?download=1", True),
        ("https://example.com/sheet.xlsx#tab", True),
        ("https://example.com/pdf", False),
        ("https://example.com/page.html?file=a.pdf", False),
    ],
)
def test_a_document_is_named_by_its_path_extension(url: str, expected: bool) -> None:
    assert is_document(url) is expected


def test_a_missing_file_is_not_fetched_again_down_the_chain(router: AutoExtractProvider) -> None:
    """A 404 is the file's own: a web backend would fetch the same 404, so the call raises without asking one."""
    with pytest.raises(WebRouterError, match="404"):
        router.extract(ExtractRequest(urls=[MISSING]))

    assert _calls(router) == ([[MISSING]], [])


def test_the_provider_is_whoever_served_not_the_parser_that_read_nothing(router: AutoExtractProvider) -> None:
    response = router.extract(ExtractRequest(urls=[BLANK, MISSING, PAGE]))

    assert response.provider == "fake"
    assert [d.url for d in response.documents] == [BLANK, PAGE]
    assert [(f.url, f.kind) for f in response.failures] == [(MISSING, ERROR_KIND_TARGET)]


def test_provider_options_skip_the_parser_rather_than_being_dropped(router: AutoExtractProvider) -> None:
    response = router.extract(ExtractRequest(urls=[PDF], provider_options={"render": True}))

    assert response.provider == "fake"
    assert _calls(router) == ([], [[PDF]])


@pytest.mark.parametrize("url", ["http://127.0.0.1/report.pdf", "http://10.0.0.5/report.pdf", "http://localhost/a.pdf"])
def test_a_private_document_url_goes_down_the_chain_not_to_the_local_parser(
    router: AutoExtractProvider, url: str
) -> None:
    router.extract(ExtractRequest(urls=[url]))

    assert _calls(router) == ([], [[url]])


def test_a_hook_that_refuses_the_parser_sends_documents_down_the_chain(router: AutoExtractProvider) -> None:
    from web_router.routing.attempts import Attempt, CallRoute, call_route

    asked: list[str] = []

    def refuse_parser(attempt: Attempt) -> bool:
        asked.append(attempt.provider)
        return attempt.provider != "fake-parser"

    with call_route(CallRoute(before_attempt=refuse_parser)):
        response = router.extract(ExtractRequest(urls=[PDF]))

    assert asked[0] == "fake-parser"
    assert response.documents[0].content == "hello"
    assert _calls(router) == ([], [[PDF]])

"""LiteParse provider tests: the file is served by a mock transport and parsed for real, in process."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from web_router import WebRouter
from web_router.exceptions import ProviderError
from web_router.providers.liteparse import LiteParseExtractProvider
from web_router.types import ExtractRequest
from web_router.types_errors import ERROR_KIND_TARGET

URL = "https://example.com/report.pdf"
LIVE_PDF = "https://arxiv.org/pdf/1706.03762v7.pdf"
TEXT = "Quarterly revenue grew"

pytestmark = pytest.mark.usefixtures("public_dns")


def _pdf(text: str) -> bytes:
    """A one-page PDF showing ``text``, with a valid cross-reference table."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (number, obj)
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return body + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def _provider(status: int = 200, body: bytes = b"", content_type: str = "application/pdf") -> LiteParseExtractProvider:
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    return LiteParseExtractProvider(transport=httpx.MockTransport(serve))


@pytest.mark.parametrize("fmt", ["markdown", "text"])
def test_a_pdf_is_parsed_into_the_format_asked_for(fmt: str) -> None:
    provider = _provider(body=_pdf(TEXT))
    try:
        response = provider.extract(ExtractRequest(urls=[URL], format=fmt))  # type: ignore[arg-type]
    finally:
        provider.close()

    document = response.documents[0]
    assert TEXT in document.content
    assert (document.url, document.format, document.status_code) == (URL, fmt, 200)
    assert document.title == ""  # a PDF has no page title to report, and none is invented
    assert document.extra == {"total_pages": 1, "content_type": "application/pdf"}


def test_markdown_is_the_default() -> None:
    provider = _provider(body=_pdf(TEXT))
    try:
        assert provider.extract(ExtractRequest(urls=[URL])).documents[0].format == "markdown"
    finally:
        provider.close()


def test_the_async_path_parses_the_same_file() -> None:
    provider = _provider(body=_pdf(TEXT))

    async def run() -> str:
        try:
            return (await provider.aextract(ExtractRequest(urls=[URL]))).documents[0].content
        finally:
            await provider.aclose()

    assert TEXT in asyncio.run(run())


def test_a_web_page_is_a_failure_not_an_empty_document() -> None:
    provider = _provider(body=b"<html><body>Sign in</body></html>", content_type="text/html")
    try:
        with pytest.raises(ProviderError, match="LiteParse could not read the file"):
            provider.extract(ExtractRequest(urls=[URL]))
    finally:
        provider.close()


def test_a_missing_file_is_the_targets_own_failure() -> None:
    provider = _provider(status=404, body=b"not found")
    try:
        with pytest.raises(ProviderError) as raised:
            provider.extract(ExtractRequest(urls=[URL]))
    finally:
        provider.close()

    assert raised.value.kind == ERROR_KIND_TARGET
    assert "not found" in str(raised.value)


MB = b"x" * 1024 * 1024


def test_a_huge_error_body_is_not_read_past_its_first_bytes() -> None:
    pulled: list[int] = []

    def body() -> Iterator[bytes]:
        for _ in range(60):  # 60 MB, past the 50 MB download cap
            pulled.append(1)
            yield MB

    provider = LiteParseExtractProvider(transport=httpx.MockTransport(lambda r: httpx.Response(500, content=body())))
    with pytest.raises(ProviderError, match="HTTP 500") as caught:
        provider.extract(ExtractRequest(urls=[URL]))

    assert len(pulled) == 1
    assert caught.value.kind != ERROR_KIND_TARGET


def test_the_async_path_reads_only_an_error_bodys_first_bytes_too() -> None:
    pulled: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for _ in range(60):
            pulled.append(1)
            yield MB

    provider = LiteParseExtractProvider(transport=httpx.MockTransport(lambda r: httpx.Response(500, content=body())))
    with pytest.raises(ProviderError, match="HTTP 500"):
        asyncio.run(provider.aextract(ExtractRequest(urls=[URL])))

    assert len(pulled) == 1


def test_html_is_refused_rather_than_relabelled() -> None:
    provider = _provider(body=_pdf(TEXT))
    try:
        with pytest.raises(Exception, match="html"):
            provider.extract(ExtractRequest(urls=[URL], format="html"))
    finally:
        provider.close()


def test_bytes_in_hand_are_parsed_without_a_fetch() -> None:
    provider = LiteParseExtractProvider()
    try:
        document = provider.parse_bytes(_pdf(TEXT), url=URL, format="text").documents[0]
    finally:
        provider.close()

    assert TEXT in document.content
    assert (document.url, document.format, document.status_code) == (URL, "text", None)


def test_a_file_past_the_size_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web_router.providers.liteparse.LITEPARSE_MAX_BYTES", 100)

    with pytest.raises(ProviderError, match="larger than"):
        _provider(body=_pdf(TEXT)).extract(ExtractRequest(urls=[URL]))
    with pytest.raises(ProviderError, match="larger than"):
        asyncio.run(_provider(body=_pdf(TEXT)).aextract(ExtractRequest(urls=[URL])))


def test_a_parse_past_its_time_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from liteparse import ParseTimeoutError

    def too_slow(self: object, data: bytes) -> None:
        raise ParseTimeoutError("parse exceeded 1s", source="<bytes>", timeout=1.0)

    monkeypatch.setattr("web_router.providers.liteparse.LiteParse.parse", too_slow)
    with pytest.raises(ProviderError) as caught:
        _provider(body=_pdf(TEXT)).extract(ExtractRequest(urls=[URL]))

    assert caught.value.kind == "timeout"


@pytest.mark.integration
def test_a_live_pdf_is_parsed_and_a_page_beside_it_goes_down_the_chain() -> None:
    """Keyless end to end: before, Nimble's keyless tier returned this PDF as its raw bytes labelled markdown."""
    with WebRouter() as router:
        response = router.extract([LIVE_PDF, "https://example.com"])

    assert response.provider == "liteparse"
    assert "Attention Is All You Need" in response.documents[0].content
    assert "documentation examples" in response.documents[1].content

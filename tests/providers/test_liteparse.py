"""LiteParse provider tests: the file is served by a mock transport and parsed for real, in process."""

from __future__ import annotations

import asyncio

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


def _redirecting(location: str, seen: list[httpx.Request]) -> LiteParseExtractProvider:
    """``URL`` redirects to ``location``, which serves the PDF; every request lands in ``seen``."""

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == URL:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, content=_pdf(TEXT), headers={"content-type": "application/pdf"})

    return LiteParseExtractProvider(transport=httpx.MockTransport(serve), headers={"X-Api-Key": "secret"})


def test_a_redirect_is_followed_without_the_callers_headers_on_another_host() -> None:
    seen: list[httpx.Request] = []
    provider = _redirecting("https://storage.example.net/report.pdf", seen)

    response = provider.extract(ExtractRequest(urls=[URL]))

    assert TEXT in response.documents[0].content
    assert [request.headers.get("X-Api-Key") for request in seen] == ["secret", None]
    assert all("Authorization" not in request.headers for request in seen)


def test_a_redirect_on_the_same_host_keeps_the_callers_headers() -> None:
    seen: list[httpx.Request] = []
    provider = _redirecting("https://example.com/files/report.pdf", seen)

    provider.extract(ExtractRequest(urls=[URL]))

    assert [request.headers.get("X-Api-Key") for request in seen] == ["secret", "secret"]


@pytest.mark.parametrize("location", ["http://127.0.0.1/admin", "http://169.254.169.254/latest", "http://10.0.0.5/x"])
def test_a_public_url_may_not_redirect_to_a_private_host(location: str) -> None:
    seen: list[httpx.Request] = []
    provider = _redirecting(location, seen)

    with pytest.raises(ProviderError) as caught:
        provider.extract(ExtractRequest(urls=[URL]))

    assert caught.value.kind == ERROR_KIND_TARGET
    assert len(seen) == 1


def test_the_async_path_refuses_a_private_redirect_too() -> None:
    seen: list[httpx.Request] = []
    provider = _redirecting("http://127.0.0.1/admin", seen)

    with pytest.raises(ProviderError):
        asyncio.run(provider.aextract(ExtractRequest(urls=[URL])))

    assert len(seen) == 1


def test_a_private_url_asked_for_directly_may_redirect_within_its_network() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "10.0.0.5":
            return httpx.Response(302, headers={"location": "http://10.0.0.6/report.pdf"})
        return httpx.Response(200, content=_pdf(TEXT), headers={"content-type": "application/pdf"})

    provider = LiteParseExtractProvider(transport=httpx.MockTransport(serve))
    response = provider.extract(ExtractRequest(urls=["http://10.0.0.5/report.pdf"]))

    assert TEXT in response.documents[0].content


def test_a_file_past_the_size_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web_router.providers.liteparse.LITEPARSE_MAX_BYTES", 100)

    with pytest.raises(ProviderError, match="larger than"):
        _provider(body=_pdf(TEXT)).extract(ExtractRequest(urls=[URL]))
    with pytest.raises(ProviderError, match="larger than"):
        asyncio.run(_provider(body=_pdf(TEXT)).aextract(ExtractRequest(urls=[URL])))


def test_a_redirect_loop_ends() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": URL})

    provider = LiteParseExtractProvider(transport=httpx.MockTransport(serve))
    with pytest.raises(ProviderError, match="redirected more than"):
        provider.extract(ExtractRequest(urls=[URL]))


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

"""LiteParse follows redirects itself: which hops it takes, and which caller headers go on each."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.providers.conftest import asked
from tests.providers.test_liteparse import TEXT, URL, _pdf
from web_router.exceptions import ProviderError
from web_router.providers.liteparse import LiteParseExtractProvider
from web_router.types import ExtractRequest
from web_router.types_errors import ERROR_KIND_TARGET

pytestmark = pytest.mark.usefixtures("public_dns")


def _redirecting(location: str, seen: list[httpx.Request]) -> LiteParseExtractProvider:
    """``URL`` redirects to ``location``, which serves the PDF; every request lands in ``seen``."""

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if asked(request) == URL:
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


def test_a_redirect_from_https_to_http_drops_the_callers_headers() -> None:
    seen: list[httpx.Request] = []
    provider = _redirecting("http://example.com/report.pdf", seen)

    provider.extract(ExtractRequest(urls=[URL]))

    assert [request.headers.get("X-Api-Key") for request in seen] == ["secret", None]


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


def test_a_redirect_loop_ends() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": URL})

    provider = LiteParseExtractProvider(transport=httpx.MockTransport(serve))
    with pytest.raises(ProviderError, match="redirected more than"):
        provider.extract(ExtractRequest(urls=[URL]))

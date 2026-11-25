"""LiteParse looks a host name up before fetching: a public-looking name that resolves to this machine is refused."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import pytest

from tests.providers.conftest import asked
from tests.providers.test_liteparse import TEXT, _pdf
from web_router.exceptions import ProviderError
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.liteparse import LiteParseExtractProvider
from web_router.types import ExtractRequest
from web_router.types_errors import ERROR_KIND_TARGET

URL = "http://127.0.0.1.nip.io:8765/x.pdf"
PUBLIC = "https://files.example.com/x.pdf"


@pytest.fixture(autouse=True)
def dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Each host's one answer; a host not listed resolves to a public address."""
    answers = {"127.0.0.1.nip.io": "127.0.0.1", "intranet.example.com": "10.0.0.5", "xn--fa-hia.example": "127.0.0.1"}

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        address = answers.get(host, "93.184.215.14")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr("web_router.providers._liteparse_hops.socket.getaddrinfo", getaddrinfo)
    return answers


def _transport(seen: list[str], redirect_to: str | None = None) -> httpx.MockTransport:
    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(asked(request))
        if redirect_to is not None and asked(request) != redirect_to:
            return httpx.Response(302, headers={"location": redirect_to})
        return httpx.Response(200, content=_pdf(TEXT), headers={"content-type": "application/pdf"})

    return httpx.MockTransport(serve)


def _serving(seen: list[str], redirect_to: str | None = None) -> LiteParseExtractProvider:
    return LiteParseExtractProvider(transport=_transport(seen, redirect_to))


def test_a_name_that_resolves_to_loopback_is_not_fetched() -> None:
    seen: list[str] = []
    with pytest.raises(ProviderError, match="may not reach") as caught:
        _serving(seen).extract(ExtractRequest(urls=[URL]))

    assert seen == []
    assert caught.value.kind != ERROR_KIND_TARGET  # not the file's fault: a chain may still try it


def test_the_async_path_looks_the_name_up_too() -> None:
    seen: list[str] = []
    with pytest.raises(ProviderError, match="may not reach"):
        asyncio.run(_serving(seen).aextract(ExtractRequest(urls=[URL])))

    assert seen == []


def test_a_unicode_name_is_looked_up_as_httpx_will_connect_to_it() -> None:
    """``faß`` is ``fass`` to IDNA 2003 (getaddrinfo's encoding) but ``xn--fa-hia`` to httpx; only the second counts."""
    seen: list[str] = []
    with pytest.raises(ProviderError, match="may not reach"):
        _serving(seen).extract(ExtractRequest(urls=["http://faß.example/x.pdf"]))

    assert seen == []


@pytest.mark.parametrize("run", ["sync", "async"])
def test_a_redirect_to_a_name_that_resolves_privately_is_not_followed(run: str) -> None:
    seen: list[str] = []
    provider = _serving(seen, redirect_to="http://intranet.example.com/x.pdf")
    request = ExtractRequest(urls=[PUBLIC])
    with pytest.raises(ProviderError, match="redirected to a host") as caught:
        provider.extract(request) if run == "sync" else asyncio.run(provider.aextract(request))

    assert seen == [PUBLIC]
    assert caught.value.kind == ERROR_KIND_TARGET


def test_a_name_that_resolves_publicly_is_still_parsed() -> None:
    seen: list[str] = []
    response = _serving(seen).extract(ExtractRequest(urls=[PUBLIC]))

    assert TEXT in response.documents[0].content
    assert seen == [PUBLIC]


@pytest.mark.parametrize("run", ["sync", "async"])
def test_under_auto_the_refused_document_goes_down_the_chain(fake_extract_provider: Any, run: str) -> None:
    seen: list[str] = []
    auto = AutoExtractProvider(chain=(("fake", {}),), members_config={"liteparse": {"transport": _transport(seen)}})
    request = ExtractRequest(urls=[URL])
    try:
        response = auto.extract(request) if run == "sync" else asyncio.run(auto.aextract(request))
    finally:
        auto.close()

    assert seen == []
    assert (response.provider, response.documents[0].content) == ("fake", "hello")

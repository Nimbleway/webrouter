"""LiteParse connects to the address it checked: a name that answers differently a moment later is never asked again."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import pytest

from tests.providers.test_liteparse import TEXT, _pdf
from web_router.exceptions import ProviderError
from web_router.providers.liteparse import LiteParseExtractProvider
from web_router.types import ExtractRequest
from web_router.types_errors import ERROR_KIND_TARGET

URL = "https://files.example.com/x.pdf"
PUBLIC_IP = "93.184.215.14"


def _rebinding(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    """Each lookup gets the next of ``answers``, the last one from then on; returns the names looked up."""
    looked_up: list[str] = []

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        looked_up.append(host)
        address = answers[min(len(looked_up), len(answers)) - 1]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr("web_router.providers._liteparse_hops.socket.getaddrinfo", getaddrinfo)
    return looked_up


def _recording(sent: list[httpx.Request], redirect_to: str | None = None) -> LiteParseExtractProvider:
    def serve(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if redirect_to is not None and len(sent) == 1:
            return httpx.Response(302, headers={"location": redirect_to})
        return httpx.Response(200, content=_pdf(TEXT), headers={"content-type": "application/pdf"})

    return LiteParseExtractProvider(transport=httpx.MockTransport(serve), headers={"Authorization": "Bearer t"})


@pytest.mark.parametrize("run", ["sync", "async"])
def test_the_fetch_goes_to_the_checked_address_not_a_second_answer(monkeypatch: pytest.MonkeyPatch, run: str) -> None:
    looked_up = _rebinding(monkeypatch, PUBLIC_IP, "127.0.0.1")
    sent: list[httpx.Request] = []
    provider, request = _recording(sent), ExtractRequest(urls=[URL])

    response = provider.extract(request) if run == "sync" else asyncio.run(provider.aextract(request))

    assert TEXT in response.documents[0].content
    assert [r.url.host for r in sent] == [PUBLIC_IP]
    assert looked_up == ["files.example.com"]  # once: nothing is left to resolve the name again


def test_the_name_stays_the_host_and_the_tls_server_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _rebinding(monkeypatch, PUBLIC_IP)
    sent: list[httpx.Request] = []

    _recording(sent).extract(ExtractRequest(urls=[URL]))

    (request,) = sent
    assert str(request.url) == f"https://{PUBLIC_IP}/x.pdf"
    assert request.headers["host"] == "files.example.com"
    assert request.extensions["sni_hostname"] == "files.example.com"  # httpcore verifies the certificate against it
    assert request.headers["authorization"] == "Bearer t"


def test_each_redirect_hop_is_pinned_to_its_own_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _rebinding(monkeypatch, PUBLIC_IP)
    sent: list[httpx.Request] = []

    _recording(sent, redirect_to="https://storage.example.net:8443/x.pdf?sig=1").extract(ExtractRequest(urls=[URL]))

    assert [str(r.url) for r in sent] == [f"https://{PUBLIC_IP}/x.pdf", f"https://{PUBLIC_IP}:8443/x.pdf?sig=1"]
    assert [r.headers["host"] for r in sent] == ["files.example.com", "storage.example.net:8443"]
    assert [r.extensions["sni_hostname"] for r in sent] == ["files.example.com", "storage.example.net"]
    assert "authorization" not in sent[1].headers


def test_a_name_that_does_not_resolve_is_not_handed_to_httpx_to_try_again(monkeypatch: pytest.MonkeyPatch) -> None:
    def fails(*args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("no such name")

    monkeypatch.setattr("web_router.providers._liteparse_hops.socket.getaddrinfo", fails)
    sent: list[httpx.Request] = []

    with pytest.raises(ProviderError, match="does not resolve") as caught:
        _recording(sent).extract(ExtractRequest(urls=[URL]))

    assert sent == []
    assert caught.value.kind != ERROR_KIND_TARGET  # a chain backend may still reach it


def test_an_ipv4_answer_is_pinned_before_an_ipv6_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def both(*args: Any, **kwargs: Any) -> list[Any]:
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0)),
        ]

    monkeypatch.setattr("web_router.providers._liteparse_hops.socket.getaddrinfo", both)
    sent: list[httpx.Request] = []

    _recording(sent).extract(ExtractRequest(urls=[URL]))

    assert sent[0].url.host == PUBLIC_IP

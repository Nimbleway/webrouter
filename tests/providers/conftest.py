"""What the provider tests share: a mock transport that records each request a backend sends."""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest


@pytest.fixture
def sent() -> list[httpx.Request]:
    """The requests :func:`transport` has received."""
    return []


@pytest.fixture
def transport(sent: list[httpx.Request]) -> httpx.MockTransport:
    """Records every request in :func:`sent` and answers each with an empty JSON object."""
    return httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json={}))


@pytest.fixture
def public_dns(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every name LiteParse looks up resolves to a public address, so no unit test does a real lookup."""
    if request.node.get_closest_marker("integration"):
        return

    def getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]

    monkeypatch.setattr("web_router.providers._liteparse_hops.socket.getaddrinfo", getaddrinfo)


def asked(request: httpx.Request) -> str:
    """The URL ``request`` asks for, by its ``Host`` header: a pinned request's own URL names the address."""
    return str(request.url.copy_with(netloc=request.headers["host"].encode("ascii")))

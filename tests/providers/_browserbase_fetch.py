"""The recorded Fetch API the Browserbase extract tests share."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from web_router.providers.browserbase_extract import BrowserbaseExtractProvider

KEY = "test-key"
URL = "https://example.com/account/orders"

FETCHED: dict[str, Any] = {
    "id": "1601dd35-1820-4ef5-8a64-26c80161edae",
    "statusCode": 200,
    "headers": {"Content-Type": "text/html; charset=utf-8", "Server": "cloudflare"},
    "contentType": "text/markdown",
    "encoding": "utf-8",
    "content": "Your orders\n\nOrder 1234",
}


class Fetch:
    """A recorded Fetch API: answers every call with the payload above and remembers what it was sent."""

    def __init__(self, payload: dict[str, Any] = FETCHED) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: list[httpx.Headers] = []
        self.payload = payload

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, json.loads(request.content)))
        self.headers.append(request.headers)
        return httpx.Response(200, json=self.payload)


def recorded_fetch(monkeypatch: pytest.MonkeyPatch) -> Fetch:
    """A recorded Fetch API with the key set: what each test file's ``fetch`` fixture returns."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    return Fetch()


def provider(api: Fetch) -> BrowserbaseExtractProvider:
    return BrowserbaseExtractProvider(transport=httpx.MockTransport(api))

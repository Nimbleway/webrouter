"""A recording transport for the HTTP live data providers: answers from a function, keeps every request."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from web_router.providers._http_live_data import HttpLiveDataProvider


class Api:
    """An httpx transport answering from ``respond(request)``, keeping each request it was sent."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self) -> Any:
        return json.loads(self.last.content)


def wire(provider: HttpLiveDataProvider, api: Api) -> HttpLiveDataProvider:
    """Point a provider's sync and async clients at ``api``."""
    transport = httpx.MockTransport(api)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return provider

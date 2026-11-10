"""What the answer provider tests share: one scripted reply behind a provider's real client settings.

An answer is one request, so the stub answers every call with the same
payload and records what was sent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from web_router.providers._http import _HttpProvider


class AnswerApi:
    """Stands in for an answer API: answers one payload and records every call."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[tuple[str, str]] = []
        self.hosts: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.hosts.append(request.url.host)
        self.bodies.append(json.loads(request.content or b"{}"))
        self.headers.append(request.headers)
        return httpx.Response(self.status, json=self.payload)


def stub_answer_api(provider: _HttpProvider, payload: dict[str, Any], status: int = 200) -> AnswerApi:
    """Answer this provider's HTTP calls with ``payload``, through its real client settings."""
    api = AnswerApi(payload, status)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api

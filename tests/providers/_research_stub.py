"""What the stubbed research tests share: a scripted API behind a provider's real client settings.

A run is started with a POST and reported with a GET, so the stub answers every
POST with the start payload and each GET with the next status in its script,
repeating the last one once the script runs out.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from web_router.providers._http import HttpResearchProvider


class ResearchApi:
    """Stands in for a Research API: answers from a script and records every call."""

    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]]) -> None:
        self._started = started
        self._states = states
        self.calls: list[tuple[str, str]] = []
        self.hosts: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.raw_paths: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.hosts.append(request.url.host)
        self.headers.append(request.headers)
        self.raw_paths.append(request.url.raw_path.decode())
        if request.method == "POST":
            self.bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json=self._started)
        return httpx.Response(200, json=self._states.pop(0) if len(self._states) > 1 else self._states[0])


def stub_research_api(
    provider: HttpResearchProvider, started: dict[str, Any], states: list[dict[str, Any]]
) -> ResearchApi:
    """Answer this provider's HTTP calls from a script, through its real client settings."""
    api = ResearchApi(started, states)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api

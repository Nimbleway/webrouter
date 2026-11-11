"""A scripted stand-in for Perplexity's Agent (Responses) API, shared by the research and citation tests."""

from __future__ import annotations

import json
from typing import Any

import httpx

from web_router.providers.perplexity import PerplexityResearchProvider

RUN_ID = "resp_3cb8ce0f-79f7-42d1-bbd4-bd503a945a93"
QUESTION = "In one sentence: what year did the Eiffel Tower open?"
ANSWER = "The Eiffel Tower opened in **1889**. [web:2]"


def run(status: str, **extra: Any) -> dict[str, Any]:
    """A response object as the Responses API reports a background run."""
    return {"id": RUN_ID, "object": "response", "status": status, "background": True, "error": None, **extra}


FINISHED_OUTPUT = [
    {
        "type": "search_results",
        "queries": ["Eiffel Tower opening year"],
        "results": [
            {
                "id": 2,
                "url": "https://en.wikipedia.org/wiki/Eiffel_Tower",
                "title": "Eiffel Tower",
                "snippet": "Opened on 31 March 1889",
                "source": "web",
                "date": "2026-01-01",
            }
        ],
    },
    {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": ANSWER, "annotations": []}],
    },
]
COMPLETED = run("completed", output=FINISHED_OUTPUT, incomplete_details=None)


class _Api:
    """Stands in for the Responses API: answers from a script and records every call."""

    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]]) -> None:
        self._started = started
        self._states = states
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.method == "POST":
            self.bodies.append(json.loads(request.content or b"{}"))
            if request.url.path.endswith("/cancel"):
                return httpx.Response(200, json={"response_id": RUN_ID, "status": "cancelling"})
            return httpx.Response(200, json=self._started)
        return httpx.Response(200, json=self._states.pop(0) if len(self._states) > 1 else self._states[0])


def stub(
    provider: PerplexityResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
) -> _Api:
    """Answer this provider's HTTP calls from a script, through its real client settings."""
    api = _Api(started or run("queued"), states or [COMPLETED])
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api

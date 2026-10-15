"""The You.com research provider, over a stubbed Research API.

The payloads here follow a live run on 2026-10-16: ``POST /v1/research`` on
``api.you.com`` with ``background`` answered ``queued`` under ``task_id``,
``GET /v1/research/{task_id}`` went through ``running`` to ``completed`` under
``id``, with the answer and its sources in ``result.output``. An unknown
``research_effort`` was a 422 naming the five tiers, the same path on the search
host ``ydc-index.io`` answered 403, and neither ``POST .../cancel`` (404) nor
``DELETE`` (405) exists. The shape of a failed run's ``error`` is from
documentation, which describes it as a diagnostic message; no live run failed.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.you import YouResearchProvider
from web_router.types import ResearchEffort, ResearchRequest

TASK_ID = "2234bb93-eaae-4a02-b82e-d89175a51812"
QUESTION = "In one sentence: what year did the Eiffel Tower open?"
ANSWER = (
    "The Eiffel Tower was inaugurated on March 31, 1889, and officially opened to the public on May 15, 1889. [[1, 2]]"
)

STARTED = {
    "task_id": TASK_ID,
    "type": "research",
    "status": "queued",
    "stream_url": f"/v1/research/{TASK_ID}/stream",
    "created_at": "2026-10-15T21:41:26.682356Z",
}


def _task(status: str, **extra: Any) -> dict[str, Any]:
    """A task as the status call reports it."""
    return {
        "id": TASK_ID,
        "task_type": "research",
        "status": status,
        "created_at": "2026-10-15T21:41:26.682356Z",
        "updated_at": "2026-10-15T21:41:33.891162Z",
        "error": None,
        "input": {"input": QUESTION, "research_effort": "lite", "background": True, "output_schema": None},
        "result": None,
        **extra,
    }


SOURCES = [
    {
        "snippets": ["The Tower would open to the public on May 15, 1889.", "In 1889, the Tower was red."],
        "title": "133 years and 330 metres - The Eiffel Tower",
        "url": "https://www.toureiffel.paris/en/news/history-and-culture/133-years-and-1083-feet",
    },
    {
        "snippets": ["Open to the public on May 15, 1889, the Tower stunned the world."],
        "title": "The Tower as the highlight of the 1889 World Exhibition - Eiffel Tower",
        "url": "https://www.toureiffel.paris/en/news/130-years/tower-highlight-1889-world-exhibition",
    },
]
COMPLETED = _task(
    "completed",
    completed_at="2026-10-15T21:41:33.891168Z",
    result={"output": {"content": ANSWER, "content_type": "text", "sources": SOURCES}, "warnings": []},
)


class _Api:
    """Stands in for the Research API: answers from a script and records every call."""

    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]]) -> None:
        self._started = started
        self._states = states
        self.calls: list[tuple[str, str]] = []
        self.hosts: list[str] = []
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.hosts.append(request.url.host)
        if request.method == "POST":
            self.bodies.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json=self._started)
        return httpx.Response(200, json=self._states.pop(0) if len(self._states) > 1 else self._states[0])


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> YouResearchProvider:
    monkeypatch.setenv("YOU_API_KEY", "test-key")
    return YouResearchProvider()


def _stub(
    provider: YouResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
) -> _Api:
    """Answer this provider's HTTP calls from a script, through its real client settings."""
    api = _Api(started or STARTED, states or [COMPLETED])
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


# -- What gets sent ----------------------------------------------------------


def test_a_run_is_a_background_task_on_the_research_host(provider: YouResearchProvider) -> None:
    api = _stub(provider)
    provider.research(ResearchRequest(query=QUESTION))

    assert api.calls[0] == ("POST", "/v1/research")
    assert api.hosts[0] == "api.you.com"
    # No effort named, so none is sent and You.com's own default applies.
    assert api.bodies[0] == {"input": QUESTION, "background": True}


@pytest.mark.parametrize(
    ("effort", "tier"),
    # `max` stops at exhaustive: frontier is background-only and priced well above it.
    [("low", "lite"), ("medium", "standard"), ("high", "deep"), ("max", "exhaustive")],
)
def test_effort_maps_onto_research_effort(provider: YouResearchProvider, effort: ResearchEffort, tier: str) -> None:
    assert provider._build_payload(ResearchRequest(query="q", effort=effort))["research_effort"] == tier


def test_a_structured_answer_is_refused_rather_than_returned_as_prose(provider: YouResearchProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="output_schema"):
        provider.research(ResearchRequest(query="q", output_schema={"type": "object", "properties": {}}))


def test_provider_options_reach_frontier(provider: YouResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", provider_options={"research_effort": "frontier"}))
    assert payload["research_effort"] == "frontier"


# -- The job cycle -----------------------------------------------------------


def test_the_run_is_started_then_polled_until_it_completes(provider: YouResearchProvider) -> None:
    api = _stub(provider, states=[_task("running"), COMPLETED])

    response = provider.research(ResearchRequest(query=QUESTION, effort="low"))

    assert [call for call in api.calls if call[0] == "GET"][-1] == ("GET", f"/v1/research/{TASK_ID}")
    assert response.answer == ANSWER
    assert response.outcome == "complete"
    assert response.request_id == TASK_ID
    assert response.raw == [COMPLETED]


def test_sources_become_citations(provider: YouResearchProvider) -> None:
    _stub(provider)
    first, second = provider.research(ResearchRequest(query=QUESTION)).citations

    assert first.url == SOURCES[0]["url"]
    assert first.title == SOURCES[0]["title"]
    assert first.snippet == "The Tower would open to the public on May 15, 1889.\n\nIn 1889, the Tower was red."
    assert first.extra == {}
    assert second.snippet == SOURCES[1]["snippets"][0]


def test_an_unrecognized_status_raises_rather_than_being_polled_forever(provider: YouResearchProvider) -> None:
    _stub(provider, started={**STARTED, "status": "thinking-hard"})
    with pytest.raises(ProviderError, match="thinking-hard"):
        provider.research(ResearchRequest(query=QUESTION))


def test_the_key_goes_out_as_youcoms_header(provider: YouResearchProvider) -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-API-Key"))
        return httpx.Response(200, json=COMPLETED)

    provider._sync = httpx.Client(**provider._client_kwargs, transport=httpx.MockTransport(handle))
    provider.research(ResearchRequest(query=QUESTION))

    assert seen and all(key == "test-key" for key in seen)


def test_a_failed_run_reports_youcoms_message(provider: YouResearchProvider) -> None:
    _stub(provider, states=[_task("failed", error="the research agent could not finish")])

    with pytest.raises(ProviderError, match="the research agent could not finish"):
        provider.research(ResearchRequest(query=QUESTION))


def test_a_run_cannot_be_cancelled(provider: YouResearchProvider) -> None:
    api = _stub(provider)
    assert provider.cancel(TASK_ID) is False
    assert api.calls == []


async def test_the_async_path_runs_the_same_cycle(provider: YouResearchProvider) -> None:
    api = _stub(provider, states=[_task("running"), COMPLETED])

    response = await provider.aresearch(ResearchRequest(query=QUESTION))

    assert response.answer == ANSWER
    assert ("GET", f"/v1/research/{TASK_ID}") in api.calls


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("YOU_API_KEY"), reason="YOU_API_KEY not set")
def test_live_research_answers_with_citations() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("you") as router, billing_aware("you"):
        response = router.research(QUESTION, effort="low")

    assert response.provider == "you"
    assert response.answer
    assert response.citations
    assert all(citation.url for citation in response.citations)

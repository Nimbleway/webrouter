"""The Perplexity research provider, over a stubbed Agent (Responses) API.

The payloads here follow a live run on 2026-09-30: ``POST /v1/responses`` with
``background`` answered ``queued``, ``GET /v1/responses/{id}`` went through
``in_progress`` to ``completed`` with a ``search_results`` and a ``message``
output, the ``low``, ``medium`` and ``xhigh`` presets were accepted, an unknown
one was a 400 "Invalid model", and ``POST .../cancel`` answered ``cancelling``
and left the run ``cancelled``. The chat endpoint this provider used to call now
answers 403, "Sonar is now the Agent API".
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.constants import PERPLEXITY_RESEARCH_DEFAULT_PRESET, PERPLEXITY_RESEARCH_PRESETS
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.perplexity import PerplexityResearchProvider
from web_router.types import ResearchEffort, ResearchRequest

RUN_ID = "resp_3cb8ce0f-79f7-42d1-bbd4-bd503a945a93"
QUESTION = "In one sentence: what year did the Eiffel Tower open?"
ANSWER = "The Eiffel Tower opened in **1889**. [web:2]"


def _run(status: str, **extra: Any) -> dict[str, Any]:
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
COMPLETED = _run("completed", output=FINISHED_OUTPUT, incomplete_details=None)


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


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> PerplexityResearchProvider:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    return PerplexityResearchProvider()


def _stub(
    provider: PerplexityResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
) -> _Api:
    """Answer this provider's HTTP calls from a script, through its real client settings."""
    api = _Api(started or _run("queued"), states or [COMPLETED])
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


# -- What gets sent ----------------------------------------------------------


def test_a_run_is_a_background_request_on_the_responses_api(provider: PerplexityResearchProvider) -> None:
    api = _stub(provider)
    provider.research(ResearchRequest(query=QUESTION))

    assert api.calls[0] == ("POST", "/v1/responses")
    assert api.bodies[0] == {"input": QUESTION, "preset": PERPLEXITY_RESEARCH_DEFAULT_PRESET, "background": True}


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max"])
def test_effort_maps_onto_perplexitys_presets(provider: PerplexityResearchProvider, effort: ResearchEffort) -> None:
    assert (
        provider._build_payload(ResearchRequest(query="q", effort=effort))["preset"]
        == PERPLEXITY_RESEARCH_PRESETS[effort]
    )


def test_the_deepest_effort_asks_for_perplexitys_deepest_preset(provider: PerplexityResearchProvider) -> None:
    assert provider._build_payload(ResearchRequest(query="q", effort="max"))["preset"] == "xhigh"


def test_a_structured_answer_is_refused_rather_than_returned_as_prose(provider: PerplexityResearchProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="output_schema"):
        provider.research(ResearchRequest(query="q", output_schema={"type": "object", "properties": {}}))


def test_provider_options_pass_through(provider: PerplexityResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", provider_options={"max_steps": 5}))
    assert payload["max_steps"] == 5


# -- The job cycle -----------------------------------------------------------


def test_the_run_is_started_then_polled_until_it_completes(provider: PerplexityResearchProvider) -> None:
    api = _stub(provider, states=[_run("in_progress"), COMPLETED])

    response = provider.research(ResearchRequest(query=QUESTION, effort="low"))

    assert [call for call in api.calls if call[0] == "GET"][-1] == ("GET", f"/v1/responses/{RUN_ID}")
    assert response.answer == ANSWER
    assert response.outcome == "complete"
    assert response.request_id == RUN_ID


def test_search_results_become_citations(provider: PerplexityResearchProvider) -> None:
    _stub(provider, started=COMPLETED)
    [citation] = provider.research(ResearchRequest(query=QUESTION)).citations

    assert citation.url == "https://en.wikipedia.org/wiki/Eiffel_Tower"
    assert (citation.title, citation.snippet) == ("Eiffel Tower", "Opened on 31 March 1889")
    assert citation.extra["date"] == "2026-01-01"


def test_a_run_perplexity_stopped_early_is_partial(provider: PerplexityResearchProvider) -> None:
    stopped = _run("incomplete", output=FINISHED_OUTPUT, incomplete_details={"reason": "max_output_tokens"})
    _stub(provider, started=stopped)

    assert provider.research(ResearchRequest(query=QUESTION)).outcome == "partial"


def test_an_unrecognized_status_raises_rather_than_being_polled_forever(provider: PerplexityResearchProvider) -> None:
    _stub(provider, started=_run("thinking-hard"))
    with pytest.raises(ProviderError, match="thinking-hard"):
        provider.research(ResearchRequest(query=QUESTION))


def test_a_failed_run_reports_the_message_of_perplexitys_error_object(provider: PerplexityResearchProvider) -> None:
    failed = _run("failed", error={"code": "server_error", "message": "the agent could not finish", "type": "error"})
    _stub(provider, started=failed)

    with pytest.raises(ProviderError, match="the agent could not finish") as caught:
        provider.research(ResearchRequest(query=QUESTION))
    assert "{" not in str(caught.value)


def test_a_run_is_cancelled_through_its_cancel_call(provider: PerplexityResearchProvider) -> None:
    api = _stub(provider)
    assert provider.cancel(RUN_ID) is True
    assert api.calls[-1] == ("POST", f"/v1/responses/{RUN_ID}/cancel")


async def test_the_async_path_runs_the_same_cycle(provider: PerplexityResearchProvider) -> None:
    api = _stub(provider, states=[_run("in_progress"), COMPLETED])

    response = await provider.aresearch(ResearchRequest(query=QUESTION))

    assert response.answer == ANSWER
    assert ("GET", f"/v1/responses/{RUN_ID}") in api.calls


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set")
def test_live_research_answers_with_citations() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("perplexity") as router, billing_aware("perplexity"):
        response = router.research(QUESTION, effort="low")

    assert response.provider == "perplexity"
    assert response.answer
    assert response.citations
    assert all(citation.url for citation in response.citations)

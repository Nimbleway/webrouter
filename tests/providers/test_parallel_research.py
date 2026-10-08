"""The Parallel research provider, over a stubbed Task API.

Verified against the live API. Every payload below was recorded from a real run
on 2026-09-18 -- one prose run and one structured run through the `lite`
processor -- so the field names asserted here are the ones Parallel returned,
not ones read off a documentation page.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.constants import (
    PARALLEL_RESEARCH_DEFAULT_EFFORT,
    PARALLEL_RESEARCH_JSON_OUTPUT,
    PARALLEL_RESEARCH_PROCESSORS,
    PARALLEL_RESEARCH_TEXT_OUTPUT,
)
from web_router.exceptions import ProviderError
from web_router.providers.parallel import ParallelResearchProvider
from web_router.types import ResearchEffort, ResearchRequest

RUN_ID = "trun_dd41b840a5514ae8a00a09bc81fb401a"

SCHEMA = {
    "type": "object",
    "properties": {"capital": {"type": "string"}, "country": {"type": "string"}},
    "required": ["capital", "country"],
    "additionalProperties": False,
}


def _run(status: str, **extra: Any) -> dict[str, Any]:
    """A task run, as the start call and the status call both return one."""
    return {
        "run_id": RUN_ID,
        "interaction_id": RUN_ID,
        "status": status,
        "is_active": status in ("queued", "running"),
        "processor": "lite",
        "metadata": {},
        "created_at": "2026-09-18T20:33:24.372403Z",
        "modified_at": "2026-09-18T20:33:32.216266Z",
        **extra,
    }


TEXT_RESULT = {
    "run": _run("completed"),
    "output": {
        "basis": [
            {
                "field": "output",
                "citations": [
                    {
                        "url": "http://britannica.com/place/France",
                        "excerpts": ["The capital of France is Paris."],
                    }
                ],
                "reasoning": "The cited reference explicitly states that the capital of France is Paris.",
                "confidence": "high",
            }
        ],
        "type": "text",
        "content": "Paris",
    },
}

JSON_RESULT = {
    "run": _run("completed"),
    "output": {
        "basis": [
            {
                "field": "capital",
                "citations": [
                    {
                        "title": "France | Britannica",
                        "url": "https://www.britannica.com/place/France",
                        "excerpts": ["The capital of France is Paris"],
                    }
                ],
                "reasoning": "The cited source explicitly states that the capital of France is Paris.",
                "confidence": "high",
            },
            {
                "field": "country",
                "citations": [
                    {
                        "title": "France | Britannica",
                        "url": "https://www.britannica.com/place/France",
                        "excerpts": ["The capital of France is Paris"],
                    }
                ],
                "reasoning": "The cited sentence explicitly identifies France as the country.",
                "confidence": "high",
            },
        ],
        "type": "json",
        "content": {"capital": "Paris", "country": "France"},
    },
}


class _Api:
    """Stands in for the Task API: answers from a script and records every call."""

    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self._started = started
        self._states = states
        self._result = result
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.method == "POST":
            self.bodies.append(json.loads(request.content))
            return httpx.Response(200, json=self._started)
        if request.url.path.endswith("/result"):
            assert self._result is not None, "the provider asked for the output of a run that never finished"
            return httpx.Response(200, json=self._result)
        return httpx.Response(200, json=self._states.pop(0) if len(self._states) > 1 else self._states[0])


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ParallelResearchProvider:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    return ParallelResearchProvider()


def _stub(
    provider: ParallelResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = TEXT_RESULT,
) -> _Api:
    """Answer this provider's HTTP calls from a script, through its real client settings."""
    api = _Api(started or _run("queued"), states or [_run("completed")], result)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


# -- What gets sent ----------------------------------------------------------


def test_the_question_is_the_input_and_a_processor_is_always_named(provider: ParallelResearchProvider) -> None:
    """A run naming no processor is a 422, so this is the one option where unset cannot mean unset."""
    assert provider._build_payload(ResearchRequest(query="what is the capital of France")) == {
        "input": "what is the capital of France",
        "processor": PARALLEL_RESEARCH_PROCESSORS[PARALLEL_RESEARCH_DEFAULT_EFFORT],
        "task_spec": {"output_schema": {"type": PARALLEL_RESEARCH_TEXT_OUTPUT}},
    }


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max"])
def test_effort_maps_onto_parallels_processors(provider: ParallelResearchProvider, effort: ResearchEffort) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", effort=effort))
    assert payload["processor"] == PARALLEL_RESEARCH_PROCESSORS[effort]


def test_output_schema_becomes_the_task_spec(provider: ParallelResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", output_schema=SCHEMA))
    assert payload["task_spec"] == {"output_schema": {"type": PARALLEL_RESEARCH_JSON_OUTPUT, "json_schema": SCHEMA}}


def test_a_prose_run_asks_for_prose_rather_than_taking_the_default(provider: ParallelResearchProvider) -> None:
    """Sending no spec gets the `auto` schema, which answers a question with `{"output": ...}`."""
    payload = provider._build_payload(ResearchRequest(query="q"))
    assert payload["task_spec"] == {"output_schema": {"type": PARALLEL_RESEARCH_TEXT_OUTPUT}}


def test_provider_options_pass_through(provider: ParallelResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", provider_options={"metadata": {"team": "search"}}))
    assert payload["metadata"] == {"team": "search"}


# -- What comes back ---------------------------------------------------------


def test_the_run_is_started_then_polled_then_read(provider: ParallelResearchProvider) -> None:
    """Parallel keeps the answer behind a third endpoint, so a finished run takes three calls."""
    api = _stub(provider, started=_run("queued"), states=[_run("completed")])

    response = provider.research(ResearchRequest(query="what is the capital of France"))

    assert api.calls == [
        ("POST", "/v1/tasks/runs"),
        ("GET", f"/v1/tasks/runs/{RUN_ID}"),
        ("GET", f"/v1/tasks/runs/{RUN_ID}/result"),
    ]
    assert response.provider == "parallel"
    assert response.query == "what is the capital of France"
    assert response.answer == "Paris"
    assert response.structured is None
    assert response.request_id == RUN_ID
    assert response.outcome == "complete", "nothing Parallel returns says a run stopped early"
    assert response.raw == [_run("completed"), TEXT_RESULT], "the status and the result, one entry per call"


def test_the_basis_becomes_a_citation_carrying_its_field_and_excerpt(provider: ParallelResearchProvider) -> None:
    _stub(provider, started=_run("completed"), result=TEXT_RESULT)

    citation = provider.research(ResearchRequest(query="q")).citations[0]

    assert citation.url == "http://britannica.com/place/France"
    assert citation.snippet == "The capital of France is Paris."
    assert citation.field == "output", "Parallel names the field even when the whole answer is one"
    assert citation.extra["confidence"] == "high"
    assert "explicitly states" in citation.extra["reasoning"]


def test_a_structured_run_answers_in_structure_and_cites_per_field(provider: ParallelResearchProvider) -> None:
    _stub(provider, started=_run("completed"), result=JSON_RESULT)

    response = provider.research(ResearchRequest(query="q", output_schema=SCHEMA))

    assert response.structured == {"capital": "Paris", "country": "France"}
    assert response.answer is None
    assert [citation.field for citation in response.citations] == ["capital", "country"]
    assert response.citations[0].title == "France | Britannica"
    assert response.urls == ["https://www.britannica.com/place/France"], "one page cited twice is one URL"


async def test_the_async_path_answers_the_same_way(provider: ParallelResearchProvider) -> None:
    api = _stub(provider, started=_run("completed"), result=TEXT_RESULT)

    response = await provider.aresearch(ResearchRequest(query="q"))

    assert response.answer == "Paris"
    assert ("GET", f"/v1/tasks/runs/{RUN_ID}/result") in api.calls


# -- Following a run ---------------------------------------------------------


def test_an_unfinished_run_is_not_asked_for_an_answer_it_does_not_have(provider: ParallelResearchProvider) -> None:
    api = _stub(provider, started=_run("queued"), states=[_run("running")], result=None)

    progress = provider.poll(ResearchRequest(query="q"), RUN_ID)

    assert progress.state == "running"
    assert progress.response is None
    assert api.calls == [("GET", f"/v1/tasks/runs/{RUN_ID}")]


def test_a_status_parallel_has_never_reported_before_is_not_passed_through(
    provider: ParallelResearchProvider,
) -> None:
    """Treating an unknown state as "still running" would poll a finished job forever."""
    _stub(provider, started=_run("thinking_about_it"))

    with pytest.raises(ProviderError, match="unrecognized run state"):
        provider.submit(ResearchRequest(query="q"))


def test_a_failed_run_reports_the_message_parallel_gave(provider: ParallelResearchProvider) -> None:
    failed = _run("failed", error={"ref_id": "abc", "message": "the run exceeded its budget"})
    _stub(provider, started=_run("queued"), states=[failed], result=None)

    progress = provider.poll(ResearchRequest(query="q"), RUN_ID)

    assert progress.state == "failed"
    assert progress.is_terminal
    assert progress.detail == "the run exceeded its budget"


def test_waiting_on_a_failed_run_raises_with_that_reason(provider: ParallelResearchProvider) -> None:
    failed = _run("failed", error={"ref_id": "abc", "message": "the run exceeded its budget"})
    _stub(provider, started=failed, result=None)

    with pytest.raises(ProviderError, match="exceeded its budget"):
        provider.research(ResearchRequest(query="q"))


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PARALLEL_API_KEY"), reason="PARALLEL_API_KEY not set")
def test_live_research_answers_with_citations() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("parallel") as router, billing_aware("parallel"):
        response = router.research("What is the capital of France?", effort="low")

    assert response.provider == "parallel"
    assert response.answer
    assert response.citations
    assert all(citation.url for citation in response.citations)

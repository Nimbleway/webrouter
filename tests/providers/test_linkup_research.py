"""The Linkup research provider, over a stubbed Research API.

Verified against the live API on 2026-09-30. One run was made, at `effort="low"`,
and followed from `submit_research` through its polls to `result()`: the start
call answered `pending`, the status calls `processing` and then `completed`
(after about 105 seconds), and the finished payload carried `output.answer` and
`output.sources`, from which the payload below is trimmed. The run's own `input`
echoed `reasoningDepth: "S"`, which is how `effort` was seen to arrive. The API
also refused, with a 400 each, a request with no `outputType`, a
`reasoningDepth` outside S, M, L and XL, and a `structured` run with no schema.

Not verified live, and documentation-derived: `reasoningDepth` M, L and XL (only
S was run, to keep the bill to $0.25), a `failed` run's `error` text, and
everything left unmapped -- `structured` output, the domain filters and dates,
and `mode`. They reach the API through `provider_options`, and structured
output and the domain filters raise as normalized options until a run has been
checked.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.providers._research_stub import ResearchApi, stub_research_api
from web_router.constants import LINKUP_RESEARCH_DEPTHS
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.linkup import LinkupResearchProvider
from web_router.types import ResearchEffort, ResearchRequest

RUN_ID = "797a668c-750c-45f3-989c-5a66a302ae57"

QUESTION = "What are the main differences between HTTP/2 and HTTP/3?"


def _run(status: str, **extra: Any) -> dict[str, Any]:
    """A research task, as the start call and the status call both return one."""
    return {
        "id": RUN_ID,
        "type": "research",
        "status": status,
        "error": None,
        "createdAt": "2026-09-30T15:09:33.822Z",
        "updatedAt": "2026-09-30T15:09:33.822Z",
        "input": {"q": QUESTION, "mode": "auto", "outputType": "sourcedAnswer", "reasoningDepth": "S"},
        "output": None,
        **extra,
    }


COMPLETED = _run(
    "completed",
    output={
        "answer": "**Executive Summary**\n\nHTTP/3 replaces TCP with QUIC, a transport built on UDP.",
        "sources": [
            {
                "name": "HTTP/2 vs HTTP/3: What Actually Changed",
                "url": "https://lycoristechnologies.com/blog/http-2-vs-http-3/",
                "favicon": "",
                "snippet": "HTTP/2 uses TCP as its transport layer, while HTTP/3 replaces TCP with QUIC.",
            },
            {
                "name": "HTTP/2 vs HTTP/3: What Actually Changed",
                "url": "https://lycoristechnologies.com/blog/http-2-vs-http-3/",
                "favicon": "",
                "snippet": "QUIC integrates the transport and cryptographic handshakes into a single round trip.",
            },
        ],
    },
)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> LinkupResearchProvider:
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    return LinkupResearchProvider()


def _stub(
    provider: LinkupResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
) -> ResearchApi:
    return stub_research_api(provider, started or _run("pending"), states or [COMPLETED])


# -- What gets sent ----------------------------------------------------------


def test_a_bare_run_sends_the_query_and_the_output_type_the_api_requires(provider: LinkupResearchProvider) -> None:
    """A request with no `outputType` is a 400, and there is no default to lean on."""
    assert provider._build_payload(ResearchRequest(query=QUESTION)) == {"q": QUESTION, "outputType": "sourcedAnswer"}


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max"])
def test_effort_maps_onto_reasoning_depth(provider: LinkupResearchProvider, effort: ResearchEffort) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", effort=effort))
    assert payload["reasoningDepth"] == LINKUP_RESEARCH_DEPTHS[effort]


def test_the_four_efforts_are_the_four_depths_linkup_offers() -> None:
    assert sorted(LINKUP_RESEARCH_DEPTHS.values()) == ["L", "M", "S", "XL"]


def test_an_unset_effort_leaves_the_depth_to_linkup(provider: LinkupResearchProvider) -> None:
    assert "reasoningDepth" not in provider._build_payload(ResearchRequest(query="q"))


def test_provider_options_pass_through(provider: LinkupResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", provider_options={"mode": "investigate"}))
    assert payload["mode"] == "investigate"


@pytest.mark.parametrize(
    "option",
    [{"include_domains": ["a.com"]}, {"exclude_domains": ["b.com"]}],
)
def test_an_option_not_yet_checked_against_a_live_run_raises_rather_than_being_dropped(
    provider: LinkupResearchProvider, option: dict[str, Any]
) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(ResearchRequest(query="q", **option))


SCHEMA = {"type": "object", "properties": {"year": {"type": "integer"}, "city": {"type": "string"}}}


def test_an_output_schema_asks_for_structured_output_with_the_schema_as_a_json_string(
    provider: LinkupResearchProvider,
) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", output_schema=SCHEMA))
    assert payload["outputType"] == "structured"
    assert json.loads(payload["structuredOutputSchema"]) == SCHEMA


def test_a_structured_run_returns_the_schemas_object_as_structured_and_no_prose(
    provider: LinkupResearchProvider,
) -> None:
    """What a live run returned on 2026-09-30: the output is the object the schema describes, and nothing else."""
    finished = _run("completed", output={"city": "Paris", "year": 1889})
    _stub(provider, started=_run("pending"), states=[finished])

    response = provider.research(ResearchRequest(query="q", output_schema=SCHEMA))

    assert response.structured == {"city": "Paris", "year": 1889}
    assert response.answer is None


def test_a_failed_run_reports_the_message_of_linkups_error_object(provider: LinkupResearchProvider) -> None:
    failed = _run("failed", error={"code": "RESEARCH_FAILED", "message": "the run could not finish"})
    _stub(provider, started=_run("pending"), states=[failed])

    with pytest.raises(ProviderError, match="the run could not finish") as caught:
        provider.research(ResearchRequest(query="q"))
    assert "{" not in str(caught.value)


# -- What comes back ---------------------------------------------------------


def test_the_run_is_started_then_polled_until_it_completes(provider: LinkupResearchProvider) -> None:
    api = _stub(provider, started=_run("pending"), states=[_run("processing"), COMPLETED])

    response = provider.research(ResearchRequest(query=QUESTION, effort="low"))

    assert api.calls == [
        ("POST", "/v1/research"),
        ("GET", f"/v1/research/{RUN_ID}"),
        ("GET", f"/v1/research/{RUN_ID}"),
    ]
    assert api.bodies == [{"q": QUESTION, "outputType": "sourcedAnswer", "reasoningDepth": "S"}]
    assert api.headers[0]["Authorization"] == "Bearer test-key"
    assert response.provider == "linkup"
    assert response.query == QUESTION
    assert response.answer.startswith("**Executive Summary**")  # type: ignore[union-attr]
    assert response.structured is None
    assert response.request_id == RUN_ID
    assert response.raw == [COMPLETED], "the payload the answer was read from, untouched"


def test_a_finished_run_needs_no_second_call_for_its_answer(provider: LinkupResearchProvider) -> None:
    api = _stub(provider, started=_run("pending"), states=[COMPLETED])

    provider.research(ResearchRequest(query=QUESTION))

    assert [method for method, _ in api.calls] == ["POST", "GET"]


def test_sources_become_citations_and_a_page_cited_twice_is_one_url(provider: LinkupResearchProvider) -> None:
    _stub(provider, started=COMPLETED)

    response = provider.research(ResearchRequest(query=QUESTION))

    assert len(response.citations) == 2
    citation = response.citations[0]
    assert citation.url == "https://lycoristechnologies.com/blog/http-2-vs-http-3/"
    assert citation.title == "HTTP/2 vs HTTP/3: What Actually Changed"
    assert citation.snippet.startswith("HTTP/2 uses TCP")
    assert citation.field is None, "Linkup cites the answer as a whole"
    assert citation.extra == {"favicon": ""}
    assert response.urls == ["https://lycoristechnologies.com/blog/http-2-vs-http-3/"]


def test_a_run_is_complete_because_linkup_never_says_it_stopped_early(provider: LinkupResearchProvider) -> None:
    _stub(provider, started=COMPLETED)
    assert provider.research(ResearchRequest(query=QUESTION)).outcome == "complete"


async def test_the_async_path_answers_the_same_way(provider: LinkupResearchProvider) -> None:
    api = _stub(provider, started=_run("pending"), states=[COMPLETED])

    response = await provider.aresearch(ResearchRequest(query=QUESTION))

    assert response.answer
    assert api.calls[-1] == ("GET", f"/v1/research/{RUN_ID}")


# -- Following a run ---------------------------------------------------------


@pytest.mark.parametrize(
    ("reported", "state"), [("pending", "queued"), ("processing", "running"), ("failed", "failed")]
)
def test_each_status_linkup_reports_maps_onto_a_state(
    provider: LinkupResearchProvider, reported: str, state: str
) -> None:
    _stub(provider, states=[_run(reported)])

    progress = provider.poll(ResearchRequest(query="q"), RUN_ID)

    assert progress.state == state
    assert progress.response is None


def test_a_status_linkup_has_never_reported_before_is_not_passed_through(
    provider: LinkupResearchProvider,
) -> None:
    """Treating an unknown state as "still running" would poll a finished job forever."""
    _stub(provider, started=_run("thinking_about_it"))

    with pytest.raises(ProviderError, match="unrecognized run state"):
        provider.submit(ResearchRequest(query="q"))


def test_waiting_on_a_failed_run_raises_with_the_reason_linkup_gave(provider: LinkupResearchProvider) -> None:
    _stub(provider, started=_run("failed", error="the research agent could not finish"))

    with pytest.raises(ProviderError, match="could not finish"):
        provider.research(ResearchRequest(query="q"))


def test_a_run_cannot_be_cancelled_because_linkup_has_no_call_for_it(provider: LinkupResearchProvider) -> None:
    api = _stub(provider)

    assert provider.cancel(RUN_ID) is False
    assert api.calls == [], "no request is made for a stop the API cannot do"


def test_a_job_id_cannot_point_the_poll_somewhere_else(provider: LinkupResearchProvider) -> None:
    api = _stub(provider, states=[COMPLETED])

    provider.poll(ResearchRequest(query="q"), "../credits/balance")

    assert api.raw_paths == ["/v1/research/..%2Fcredits%2Fbalance"]


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("LINKUP_API_KEY"), reason="LINKUP_API_KEY not set")
def test_live_research_answers_with_citations() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("linkup") as router, billing_aware("linkup"):
        response = router.research("What is the capital of France?", effort="low")

    assert response.provider == "linkup"
    assert response.answer
    assert response.citations
    assert all(citation.url for citation in response.citations)

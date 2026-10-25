"""The Nimble research provider, over a stubbed Web Search Agent client.

The payloads below are shaped after the SDK's generated response models, which
are the source of truth for the fields this provider reads.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from web_router.constants import (
    NIMBLE_RESEARCH_AGENT_NAMES,
    NIMBLE_RESEARCH_EFFORT,
    NIMBLE_RESEARCH_SOURCE_GROUP,
    NIMBLE_RESEARCH_USE_CASE_ENRICHMENT,
    NIMBLE_RESEARCH_USE_CASE_RESEARCH,
)
from web_router.exceptions import MissingParameterError, ProviderError, UnsupportedParameterError
from web_router.types import ResearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble import NimbleResearchProvider

AGENT_ID = "agent-1"
RUN_ID = "task_run_9"

SCHEMA = {"type": "object", "properties": {"founded": {"type": "string"}}}


def _run(status: str, **extra: Any) -> dict[str, Any]:
    """A run object, as `agents.run()` and `runs.get()` both return one."""
    return {
        "id": RUN_ID,
        "web_search_agent_id": AGENT_ID,
        "status": status,
        "is_active": status in ("queued", "running"),
        "effort": "medium",
        "interaction_id": "interaction-1",
        "created_at": "2026-01-01T00:00:00Z",
        **extra,
    }


TEXT_RESULT = {
    "run": _run("completed"),
    "output": {
        "type": "text",
        "content": "Nimbleway was founded in 2019.[1]",
        "trust": {
            "confidence": "high",
            "reasoning": "two primary sources agree",
            "claims": [
                {
                    "callout": 1,
                    "confidence": "high",
                    "reasoning": "stated on the company site",
                    "citations": [
                        {
                            "url": "https://a.example/about",
                            "title": "About",
                            "excerpts": ["Founded in 2019", "In Tel Aviv"],
                            "source_type": "primary",
                        },
                        # The same page cited twice for one claim is one citation.
                        {"url": "https://a.example/about", "title": "About", "excerpts": ["Founded in 2019"]},
                    ],
                },
                {
                    "callout": 2,
                    "confidence": "medium",
                    "reasoning": "repeated in coverage",
                    "citations": [{"url": "https://a.example/about", "title": "About", "excerpts": []}],
                },
            ],
            "sources": [
                {"url": "https://a.example/about", "title": "About", "type": "primary"},
                {"url": "https://b.example/news", "title": "News", "type": "secondary"},
            ],
        },
    },
}

JSON_RESULT = {
    "run": _run("completed"),
    "output": {
        "type": "json",
        "content": {"founded": "2019"},
        "trust": {
            "confidence": "high",
            "reasoning": "primary source",
            "claims": [
                {
                    "path": "$.founded",
                    "confidence": "high",
                    "reasoning": "stated on the company site",
                    "citations": [
                        {"url": "https://a.example/about", "title": "About", "excerpts": ["Founded in 2019"]}
                    ],
                }
            ],
            "sources": [{"url": "https://a.example/about", "title": "About", "type": "primary"}],
        },
    },
}


class _Model:
    """Stands in for an SDK response model, which the provider reads through ``model_dump``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


class _Runs:
    def __init__(self, states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self._states = states
        self._result = result
        self.gets: list[tuple[str, str]] = []
        self.results: list[tuple[str, str]] = []

    def get(self, run_id: str, *, agent_id: str) -> _Model:
        self.gets.append((agent_id, run_id))
        return _Model(self._states.pop(0) if len(self._states) > 1 else self._states[0])

    def result(self, run_id: str, *, agent_id: str) -> _Model:
        self.results.append((agent_id, run_id))
        assert self._result is not None, "the provider asked for an output this run never produced"
        return _Model(self._result)


class _Agents:
    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self._started = started
        self.runs = _Runs(states, result)
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> _Model:
        self.calls.append(kwargs)
        return _Model(self._started)


class _Client:
    def __init__(self, started: dict[str, Any], states: list[dict[str, Any]], result: dict[str, Any] | None) -> None:
        self.agents = _Agents(started, states, result)
        # Every call the provider makes goes through with_options, which is how
        # the caller's remaining research budget reaches the SDK. Recorded
        # rather than ignored so a test can prove the clamp is applied.
        self.timeouts: list[float] = []

    def with_options(self, *, timeout: float, **_: Any) -> _Client:
        self.timeouts.append(timeout)
        return self


class _AsyncRuns:
    def __init__(self, runs: _Runs) -> None:
        self._runs = runs

    async def get(self, run_id: str, *, agent_id: str) -> _Model:
        return self._runs.get(run_id, agent_id=agent_id)

    async def result(self, run_id: str, *, agent_id: str) -> _Model:
        return self._runs.result(run_id, agent_id=agent_id)


class _AsyncAgents:
    def __init__(self, agents: _Agents) -> None:
        self._agents = agents
        self.runs = _AsyncRuns(agents.runs)

    async def run(self, **kwargs: Any) -> _Model:
        return self._agents.run(**kwargs)


class _AsyncClient:
    """The async twin of the stub, recording into the same call log."""

    def __init__(self, client: _Client) -> None:
        self.agents = _AsyncAgents(client.agents)
        self._client = client

    def with_options(self, *, timeout: float, **_: Any) -> _AsyncClient:
        self._client.timeouts.append(timeout)
        return self


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleResearchProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleResearchProvider()


def _stub(
    provider: NimbleResearchProvider,
    *,
    started: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = TEXT_RESULT,
) -> _Client:
    client = _Client(started or _run("queued"), states or [_run("completed")], result)
    provider._client = client  # type: ignore[assignment]
    provider._async_client = _AsyncClient(client)  # type: ignore[assignment]
    return client


# -- What gets sent ----------------------------------------------------------


def test_a_bare_question_is_a_research_run_against_the_research_agent(provider: NimbleResearchProvider) -> None:
    assert provider._build_payload(ResearchRequest(query="who founded it")) == {
        "input": "who founded it",
        "use_case": NIMBLE_RESEARCH_USE_CASE_RESEARCH,
        "agent_name": NIMBLE_RESEARCH_AGENT_NAMES[NIMBLE_RESEARCH_USE_CASE_RESEARCH],
    }


def test_records_turn_the_run_into_an_enrichment_under_its_own_agent(provider: NimbleResearchProvider) -> None:
    """Nimble refuses a run whose use case disagrees with the agent it names, so the two move together."""
    records = [{"name": "Nimbleway"}]
    payload = provider._build_payload(ResearchRequest(query="fill these in", records=records, output_schema=SCHEMA))

    assert payload["input_data"] == records
    assert payload["use_case"] == NIMBLE_RESEARCH_USE_CASE_ENRICHMENT
    assert payload["agent_name"] == NIMBLE_RESEARCH_AGENT_NAMES[NIMBLE_RESEARCH_USE_CASE_ENRICHMENT]


def test_an_enrichment_sends_the_row_schema_as_the_table_nimble_takes(provider: NimbleResearchProvider) -> None:
    """Nimble answers 422 for an enrichment whose schema is not an array of rows."""
    payload = provider._build_payload(ResearchRequest(query="q", records=[{"name": "n"}], output_schema=SCHEMA))
    assert payload["output_schema"] == {"type": "array", "items": SCHEMA}


def test_a_nested_models_definitions_stay_where_its_references_point(provider: NimbleResearchProvider) -> None:
    """Pydantic puts a nested model under the root's `$defs`; `#/$defs/...` must still resolve once wrapped."""
    from pydantic import BaseModel

    class Office(BaseModel):
        city: str

    class Company(BaseModel):
        name: str
        office: Office

    row = Company.model_json_schema()
    payload = provider._build_payload(ResearchRequest(query="q", records=[{"name": "n"}], output_schema=row))

    schema = payload["output_schema"]
    assert schema["$defs"] == row["$defs"]
    assert "$defs" not in schema["items"]
    assert schema["items"]["properties"]["office"] == {"$ref": "#/$defs/Office"}


def test_a_table_schema_is_sent_as_it_is(provider: NimbleResearchProvider) -> None:
    table = {"type": "array", "items": SCHEMA}
    payload = provider._build_payload(ResearchRequest(query="q", records=[{"name": "n"}], output_schema=table))
    assert payload["output_schema"] == table


def test_a_question_keeps_its_schema_as_it_is(provider: NimbleResearchProvider) -> None:
    assert provider._build_payload(ResearchRequest(query="q", output_schema=SCHEMA))["output_schema"] == SCHEMA


def test_records_without_a_schema_are_refused_before_a_run_is_billed(provider: NimbleResearchProvider) -> None:
    with pytest.raises(MissingParameterError, match="output_schema"):
        provider.validate(ResearchRequest(query="q", records=[{"name": "n"}]))


def test_an_enrichment_at_low_effort_is_refused_before_a_run_is_billed(provider: NimbleResearchProvider) -> None:
    request = ResearchRequest(query="q", records=[{"name": "n"}], output_schema=SCHEMA, effort="low")
    with pytest.raises(UnsupportedParameterError, match="effort='low'"):
        provider.validate(request)
    provider.validate(request.model_copy(update={"effort": "medium"}))


def test_the_two_use_cases_do_not_share_an_agent(provider: NimbleResearchProvider) -> None:
    research = provider._build_payload(ResearchRequest(query="q"))
    enrichment = provider._build_payload(ResearchRequest(query="q", records=[{"name": "n"}]))
    assert research["agent_name"] != enrichment["agent_name"]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max"])
def test_effort_maps_onto_nimbles_own_tiers(provider: NimbleResearchProvider, effort: Any) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", effort=effort))
    assert payload["effort"] == NIMBLE_RESEARCH_EFFORT[effort]


def test_an_unset_option_is_never_sent(provider: NimbleResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q"))
    assert not {"effort", "output_schema", "input_data", "sources"} & set(payload)


def test_only_the_half_of_the_source_guidance_that_was_set_is_sent(provider: NimbleResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", include_domains=["a.com"]))
    assert payload["sources"] == {"allow": [{"title": NIMBLE_RESEARCH_SOURCE_GROUP, "domains": ["a.com"]}]}
    assert "block" not in payload["sources"]


def test_both_domain_filters_become_allow_and_block_groups(provider: NimbleResearchProvider) -> None:
    payload = provider._build_payload(
        ResearchRequest(query="q", include_domains=["a.com"], exclude_domains=["b.com", "c.com"])
    )
    assert payload["sources"]["allow"][0]["domains"] == ["a.com"]
    assert payload["sources"]["block"][0]["domains"] == ["b.com", "c.com"]


def test_provider_options_pass_through(provider: NimbleResearchProvider) -> None:
    payload = provider._build_payload(ResearchRequest(query="q", provider_options={"skill": "deep-dive"}))
    assert payload["skill"] == "deep-dive"


# -- What comes back ---------------------------------------------------------


def test_a_prose_run_answers_in_text_with_its_sources(provider: NimbleResearchProvider) -> None:
    client = _stub(provider, started=_run("completed"), result=TEXT_RESULT)

    response = provider.research(ResearchRequest(query="when was it founded"))

    assert response.provider == "nimble"
    assert response.query == "when was it founded"
    assert response.answer == "Nimbleway was founded in 2019.[1]"
    assert response.structured is None
    assert response.request_id == RUN_ID
    assert response.outcome == "complete", "nothing Nimble returns says a run stopped early"
    assert response.raw == [_run("completed"), TEXT_RESULT]
    assert client.agents.runs.results == [(AGENT_ID, RUN_ID)]


def test_claims_are_cited_per_callout_and_uncited_sources_follow(provider: NimbleResearchProvider) -> None:
    """Both halves of the trust payload matter: what backs each claim, and what else was read."""
    _stub(provider, started=_run("completed"), result=TEXT_RESULT)

    citations = provider.research(ResearchRequest(query="q")).citations

    assert [(citation.url, citation.field) for citation in citations] == [
        ("https://a.example/about", "1"),
        ("https://a.example/about", "2"),
        ("https://b.example/news", None),
    ], "one citation per (page, claim), then the sources nothing cited"
    assert citations[0].snippet == "Founded in 2019"
    assert citations[0].title == "About"
    assert citations[0].extra["excerpts"] == ["Founded in 2019", "In Tel Aviv"], "every excerpt is preserved"
    assert citations[2].extra["type"] == "secondary"


def test_the_cited_urls_are_reported_once_each(provider: NimbleResearchProvider) -> None:
    _stub(provider, started=_run("completed"), result=TEXT_RESULT)
    assert provider.research(ResearchRequest(query="q")).urls == [
        "https://a.example/about",
        "https://b.example/news",
    ]


def test_a_structured_run_answers_in_structure_and_cites_by_path(provider: NimbleResearchProvider) -> None:
    _stub(provider, started=_run("completed"), result=JSON_RESULT)

    response = provider.research(ResearchRequest(query="q", output_schema=SCHEMA))

    assert response.structured == {"founded": "2019"}
    assert response.answer is None
    assert [citation.field for citation in response.citations] == ["$.founded"]


async def test_the_async_path_answers_the_same_way(provider: NimbleResearchProvider) -> None:
    client = _stub(provider, started=_run("completed"), result=TEXT_RESULT)

    response = await provider.aresearch(ResearchRequest(query="q"))

    assert response.answer == "Nimbleway was founded in 2019.[1]"
    assert client.agents.runs.results == [(AGENT_ID, RUN_ID)]


# -- Following a run ---------------------------------------------------------


def test_a_poll_goes_back_to_the_agent_the_run_belongs_to(provider: NimbleResearchProvider) -> None:
    """A run is addressed as (agent, run), so the job id has to carry both."""
    client = _stub(provider, started=_run("queued"), states=[_run("running")])

    job = provider.submit(ResearchRequest(query="q"))
    provider.poll(ResearchRequest(query="q"), job.job_id)

    assert job.state == "queued"
    assert client.agents.runs.gets == [(AGENT_ID, RUN_ID)]
    assert not client.agents.runs.results, "an unfinished run has no output to ask for"


def test_a_job_id_from_somewhere_else_is_refused(provider: NimbleResearchProvider) -> None:
    _stub(provider)
    with pytest.raises(ProviderError, match="not a Nimble research job id"):
        provider.poll(ResearchRequest(query="q"), "just-a-run-id")


def test_a_failed_run_reports_the_backends_reason_rather_than_an_empty_answer(
    provider: NimbleResearchProvider,
) -> None:
    failed = _run("failed", error={"message": "the agent ran out of budget", "ref_id": RUN_ID})
    client = _stub(provider, started=_run("queued"), states=[failed], result=None)

    job = provider.submit(ResearchRequest(query="q"))
    progress = provider.poll(ResearchRequest(query="q"), job.job_id)

    assert progress.state == "failed"
    assert progress.is_terminal
    assert progress.response is None
    assert progress.detail == "the agent ran out of budget"
    assert not client.agents.runs.results, "Nimble refuses the output of a failed run"


def test_waiting_on_a_failed_run_raises_with_that_reason(provider: NimbleResearchProvider) -> None:
    failed = _run("failed", error={"message": "the agent ran out of budget", "ref_id": RUN_ID})
    _stub(provider, started=failed, result=None)

    with pytest.raises(ProviderError, match="ran out of budget"):
        provider.research(ResearchRequest(query="q"))


def test_a_status_nimble_has_never_reported_before_is_not_passed_through(provider: NimbleResearchProvider) -> None:
    """Treating an unknown state as "still running" would poll a finished job forever."""
    _stub(provider, started=_run("paused"))

    with pytest.raises(ProviderError, match="unrecognized run state"):
        provider.submit(ResearchRequest(query="q"))


def test_nimble_cannot_cancel_a_run(provider: NimbleResearchProvider) -> None:
    """The API exposes no way to stop one, and claiming otherwise would strand a paid run."""
    _stub(provider)
    assert provider.cancel(f"{AGENT_ID}/{RUN_ID}") is False


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_research_answers_with_citations() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("nimble") as router, billing_aware("nimble"):
        response = router.research("When was Nimbleway founded, and where is it based?", effort="low")

    assert response.provider == "nimble"
    assert response.answer
    assert response.citations
    assert all(citation.url for citation in response.citations)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_enrichment_fills_in_a_row_schema_per_record() -> None:
    """The README's enrichment, as written: a row model, two records, Nimble's default effort."""
    from pydantic import BaseModel

    from tests.conftest import billing_aware
    from web_router import WebRouter

    class Company(BaseModel):
        name: str
        what_they_sell: str
        headquarters: str | None = None

    with WebRouter("nimble") as router, billing_aware("nimble"):
        response = router.research(
            "fill in what each company sells and where it is headquartered",
            output_schema=Company,
            records=[{"name": "Nimble"}, {"name": "Tavily"}],
        )

    assert response.provider == "nimble"
    assert isinstance(response.structured, list), f"not one row per record: {response.structured!r}"
    assert [row["name"] for row in response.structured] == ["Nimble", "Tavily"]
    assert all(row["what_they_sell"] for row in response.structured)

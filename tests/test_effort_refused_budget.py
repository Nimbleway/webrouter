"""A budget never maps a research effort the backend refuses for the request: it picks one the backend takes.

Nimble refuses ``low`` on an enrichment (``records``), and its efforts list at
$0.025 (low), $0.10 (medium) and $0.50 (high). Under a $0.10 cap the budget
must pick ``medium`` -- on a named backend, in the candidates a router or a
plan is offered, and for ``submit_research`` -- and not ``low``, which would
raise or drop it. Offline: the backend's start is replaced, so nothing is sent.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from web_router import Budget, WebRouter
from web_router.providers.nimble import NimbleResearchProvider
from web_router.routing import params
from web_router.routing.candidates import build_candidates
from web_router.routing.params import EffortChoice, map_effort
from web_router.types import ResearchProgress, ResearchRequest, ResearchResponse

RECORDS = [{"name": "a"}]
SCHEMA = {"type": "object"}
ENRICH = ResearchRequest(query="q", records=RECORDS, output_schema=SCHEMA)
CAP = Budget(max_cost_usd=0.10)


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ResearchRequest]]:
    """Nimble alone keyed for research, its start logged and answered at once."""
    set_keys(clear_keys(monkeypatch), "nimble")
    log: list[ResearchRequest] = []

    def start(self: Any, request: ResearchRequest) -> ResearchProgress:
        log.append(request)
        response = ResearchResponse(provider="nimble", query=request.query, answer="a")
        return ResearchProgress(job_id="job-1", state="completed", response=response)

    monkeypatch.setattr(NimbleResearchProvider, "_start", start)
    yield log


def test_the_mapping_leaves_out_an_effort_the_backend_refuses_for_the_request() -> None:
    assert map_effort("nimble", CAP).effort == "low", "a question takes low: the cheapest that fits"
    choice = map_effort("nimble", CAP, request=ENRICH)
    assert choice.effort == "medium", "an enrichment refuses low, and medium fits $0.10"


def test_a_named_backend_runs_at_the_cheapest_effort_it_takes(started: list[ResearchRequest]) -> None:
    response = WebRouter().research("q", provider="nimble", records=RECORDS, output_schema=SCHEMA, budget=CAP)
    assert [request.effort for request in started] == ["medium"]
    assert response.decision is not None and response.decision.params == {"effort": "medium"}


def test_a_job_started_under_a_shared_budget_takes_the_effort_too(started: list[ResearchRequest]) -> None:
    wr = WebRouter()
    with wr.budget(CAP):
        job = wr.submit_research("q", provider="nimble", records=RECORDS, output_schema=SCHEMA)
    assert [request.effort for request in started] == ["medium"]
    assert job.decision is not None and job.decision.params == {"effort": "medium"}


def test_a_plan_offers_the_backend_at_the_effort_it_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "nimble")
    found = build_candidates(WebRouter(), {"research": ENRICH}, budget=CAP)
    assert found.effort is not None and found.effort.effort == "medium"
    assert [c.id for c in found.candidates] == ["research:nimble"]


def test_the_shared_effort_is_one_every_backend_in_the_order_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    picks = {"first": "low", "second": "medium"}
    monkeypatch.setattr(params, "map_effort", lambda p, *_, **__: EffortChoice(effort=picks[p], source=None, reason=""))
    monkeypatch.setattr(params, "effort_misfit", lambda *_: None)
    monkeypatch.setattr(params, "refused", lambda p, e, _request: (p, e) == ("second", "low"))
    choice = params.shared_effort(list(picks), CAP, ENRICH)
    assert choice is not None and choice.effort == "medium", "second refuses first's low: second's own pick runs"

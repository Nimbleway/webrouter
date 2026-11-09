"""The decision record: typed, every field optional, and on every response a call can route."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from web_router import (
    AnswerResponse,
    BrowseResponse,
    DecisionAttempt,
    DecisionHop,
    DecisionRecord,
    ExtractResponse,
    ResearchResponse,
    SearchResponse,
)

_RESPONSES: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (SearchResponse, {"provider": "p", "query": "q"}),
    (ExtractResponse, {"provider": "p"}),
    (AnswerResponse, {"provider": "p", "query": "q"}),
    (ResearchResponse, {"provider": "p", "query": "q"}),
    (BrowseResponse, {"provider": "p", "task": "t"}),
]


def test_every_field_is_optional() -> None:
    record = DecisionRecord()
    assert all(value is None for value in record.model_dump().values())


def test_a_full_record_round_trips_through_json() -> None:
    record = DecisionRecord(
        decision_id="d-1",
        router="rules",
        offered=["extract:firecrawl", "extract:jina", "search:exa"],
        ranked=["extract:jina", "extract:firecrawl"],
        merged=["extract:jina", "extract:firecrawl"],
        ran=1,
        hops=[DecisionHop(id="extract:jina", error="429")],
        attempts=[DecisionAttempt(id="extract:firecrawl", est_cost_usd=0.001, seconds=2.3)],
        reason="cheapest that reads pages",
        router_timeout=False,
        router_ms=812,
        dropped={"search:exa": "over the cost cap"},
        excluded={},
        params={"format": "markdown"},
        params_source="router",
        estimated_cost_usd=0.001,
        cost_is_estimate=True,
        unmeasured=["extract:jina"],
        prompt_sha256="0" * 64,
    )
    assert DecisionRecord.model_validate_json(record.model_dump_json()) == record


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        DecisionRecord(task_label="x")  # type: ignore[call-arg]


def test_params_source_takes_only_its_three_values() -> None:
    with pytest.raises(ValidationError):
        DecisionRecord(params_source="guess")  # type: ignore[arg-type]


@pytest.mark.parametrize(("model", "fields"), _RESPONSES, ids=lambda value: getattr(value, "__name__", ""))
def test_every_routable_response_carries_a_decision_that_defaults_to_none(
    model: type[BaseModel], fields: dict[str, Any]
) -> None:
    assert model(**fields).decision is None  # type: ignore[attr-defined]
    routed = model(**fields, decision=DecisionRecord(router="rules", ran=0))
    assert model.model_validate_json(routed.model_dump_json()).decision == DecisionRecord(router="rules", ran=0)  # type: ignore[attr-defined]

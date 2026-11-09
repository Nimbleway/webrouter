"""The router wire format, schema 1: the shipped JSON Schema files, and a request and a decision round-tripped."""

from __future__ import annotations

import json
from typing import Any

import pytest

from web_router import Budget, Candidate, RouteDecision, RouteRequest
from web_router.constants_remote import WIRE_REQUEST_FILE, WIRE_RESPONSE_FILE
from web_router.routing.wire import (
    WireError,
    decision_from_wire,
    decision_to_wire,
    published,
    rendered,
    request_from_wire,
    request_schema,
    request_to_wire,
    response_schema,
)

REQUEST = RouteRequest(
    prompt="compare https://a.example and https://b.example",
    candidates=(
        Candidate(tool="extract", provider="jina", est_latency_s=2.1),
        Candidate(tool="search", provider="tavily", est_cost_usd=0.008, est_latency_s=1.2),
        Candidate(tool="answer", provider="perplexity", est_cost_usd=0.005),
    ),
    deadline_s=0.8,
    urls=("https://a.example", "https://b.example"),
    budget=Budget(max_cost_usd=0.05, time_limit_s=30.0),
    pinned_tool=None,
)

DECISION = RouteDecision(
    tools=["extract", "search"],
    providers={"extract": ["jina"], "search": ["tavily", "exa"]},
    exclude={"answer:perplexity": "too slow"},
    params={"search": {"max_results": 5}},
    confidence=0.75,
    reason="names URLs",
)


@pytest.mark.parametrize("name", [WIRE_REQUEST_FILE, WIRE_RESPONSE_FILE])
def test_the_shipped_schema_files_are_what_the_types_generate(name: str) -> None:
    assert published(name) == rendered()[name], "regenerate with: uv run python -m web_router.routing.wire"


def test_the_schemas_carry_the_version_and_the_contract_field_names() -> None:
    request, response = request_schema(), response_schema()
    for schema in (request, response):
        assert schema["properties"]["schema"]["const"] == 1 and "schema" in schema["required"]
    assert {"prompt", "candidates", "deadline_s", "urls", "budget", "pinned_tool"} <= set(request["properties"])
    assert set(request["$defs"]["Budget"]["properties"]) == {"max_cost_usd", "time_limit_s", "strict"}
    assert "answer" in request["$defs"]["Candidate"]["properties"]["tool"]["enum"]
    assert set(response["properties"]) == {"schema", "tools", "providers", "exclude", "params", "confidence", "reason"}
    assert "additionalProperties" not in response, "a service may add fields; the client ignores them"
    assert {"type": "number", "minimum": 0, "maximum": 1} in response["properties"]["confidence"]["anyOf"]
    assert all(
        {"type": "null"} in response["properties"][name]["anyOf"] for name in response["properties"] if name != "schema"
    )


def test_a_request_round_trips_through_json() -> None:
    wire = json.loads(json.dumps(request_to_wire(REQUEST)))
    assert wire["schema"] == 1 and wire["budget"]["time_limit_s"] == 30.0
    assert wire["candidates"][0] == {
        "tool": "extract",
        "provider": "jina",
        "est_cost_usd": None,
        "est_latency_s": 2.1,
        "id": "extract:jina",
    }
    assert request_from_wire(wire) == REQUEST


def test_a_decision_round_trips_through_json() -> None:
    wire = json.loads(json.dumps(decision_to_wire(DECISION)))
    assert wire["schema"] == 1
    back = decision_from_wire(wire)
    assert (back.tools, back.providers, back.exclude, back.params) == (
        DECISION.tools,
        DECISION.providers,
        DECISION.exclude,
        DECISION.params,
    )
    assert (back.confidence, back.reason) == (0.75, "names URLs")


def test_unknown_fields_are_ignored_on_both_sides() -> None:
    decision = decision_from_wire({"schema": 1, "tools": ["search"], "model": "v7", "latency_ms": 3})
    assert decision == RouteDecision(tools=["search"])
    request = request_from_wire({**request_to_wire(REQUEST), "tenant": "acme"})
    assert request == REQUEST


def test_a_decision_with_nulls_or_nothing_set_is_no_opinion() -> None:
    assert decision_from_wire({"schema": 1}) == RouteDecision()
    assert decision_from_wire({"schema": 1, "exclude": None, "tools": None}) == RouteDecision()


@pytest.mark.parametrize("version", [2, 0, "1", True, None, 1.5])
def test_another_major_version_is_refused(version: Any) -> None:
    body = {"tools": ["search"]} if version is None else {"schema": version, "tools": ["search"]}
    with pytest.raises(WireError, match="wire schema"):
        decision_from_wire(body)


@pytest.mark.parametrize(
    "body",
    [
        {"schema": 1, "tools": "search"},
        {"schema": 1, "tools": ["teleport"]},
        {"schema": 1, "providers": {"search": "exa"}},
        {"schema": 1, "exclude": ["search:exa"]},
        {"schema": 1, "confidence": "very"},
    ],
)
def test_a_decision_that_breaks_the_types_is_refused(body: dict[str, Any]) -> None:
    with pytest.raises(WireError, match="does not fit"):
        decision_from_wire(body)


@pytest.mark.parametrize("body", [[], "schema", 1, None])
def test_anything_but_an_object_is_refused(body: Any) -> None:
    with pytest.raises(WireError, match="JSON object"):
        decision_from_wire(body)


def test_a_service_reading_a_bad_request_gets_a_wire_error() -> None:
    with pytest.raises(WireError):
        request_from_wire({"schema": 1, "prompt": "x"})

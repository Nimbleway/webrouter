"""The router wire format, schema 1: the shipped JSON Schema files, and a request and a decision round-tripped."""

from __future__ import annotations

import json
from typing import Any

import pytest

from web_router import Budget, Candidate, RouteDecision, RouteRequest
from web_router.constants_remote import WIRE_MAX_NAMES, WIRE_REQUEST_FILE, WIRE_RESPONSE_FILE
from web_router.routing.wire import (
    WireError,
    WireVersionError,
    decision_from_wire,
    decision_to_wire,
    request_from_wire,
    request_to_wire,
)
from web_router.routing.wire_schema import published, rendered, request_schema, response_schema

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
    assert published(name) == rendered()[name], "regenerate with: uv run python -m web_router.routing.wire_schema"


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


def _limits(node: Any) -> list[tuple[str, int | None]]:
    """Each list and mapping in a schema, with the entry limit it publishes."""
    found: list[tuple[str, int | None]] = []
    if isinstance(node, dict):
        if node.get("type") == "array":
            found.append(("array", node.get("maxItems")))
        elif node.get("type") == "object":
            found.append(("object", node.get("maxProperties")))
        for value in node.values():
            found += _limits(value)
    elif isinstance(node, list):
        for value in node:
            found += _limits(value)
    return found


def test_the_response_schema_publishes_the_entry_limit_the_client_holds() -> None:
    fields = response_schema()["properties"]
    limits = _limits(fields)
    assert limits and all(limit == WIRE_MAX_NAMES for _, limit in limits)
    over = {"schema": 1, "exclude": {f"search:p{i}": "why" for i in range(WIRE_MAX_NAMES + 1)}}
    for body in (over, {"schema": 1, "tools": ["search"] * (WIRE_MAX_NAMES + 1)}):
        with pytest.raises(WireError, match=f"more than {WIRE_MAX_NAMES} entries"):
            decision_from_wire(body)
    at = {"schema": 1, "exclude": {f"search:p{i}": "why" for i in range(WIRE_MAX_NAMES)}}
    assert len(decision_from_wire(at).exclude) == WIRE_MAX_NAMES


def test_the_request_schema_holds_the_budget_caps_to_zero_or_more() -> None:
    budget = request_schema()["$defs"]["Budget"]["properties"]
    for name in ("max_cost_usd", "time_limit_s"):
        assert {"type": "number", "minimum": 0} in budget[name]["anyOf"]
    wire = request_to_wire(REQUEST)
    wire["budget"]["max_cost_usd"] = -1
    with pytest.raises(WireError):
        request_from_wire(wire)


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


@pytest.mark.parametrize("version", ["1", True, None, "https://session.example/secret"])
def test_a_missing_or_ill_typed_schema_is_a_wire_error_without_echoing_it(version: Any) -> None:
    body = {"tools": ["search"]} if version is None else {"schema": version, "tools": ["search"]}
    with pytest.raises(WireError, match="wire schema that is missing or not a number") as refused:
        decision_from_wire(body)
    assert "secret" not in str(refused.value)


@pytest.mark.parametrize("version", [2, 0, 1.5, 9_999_999])
def test_another_major_version_is_a_version_error_without_echoing_it(version: Any) -> None:
    with pytest.raises(WireVersionError, match="version other than 1") as refused:
        decision_from_wire({"schema": version, "tools": ["search"]})
    assert not isinstance(refused.value, WireError), "a mismatch, not an invalid answer"
    assert str(version) not in str(refused.value).replace("other than 1", "")
    with pytest.raises(WireError, match="version other than 1"):
        request_from_wire({"schema": version, "prompt": "x", "candidates": [], "deadline_s": 1.0})


@pytest.mark.parametrize(
    "body",
    [
        {"schema": 1, "tools": ["teleport"]},
        {"schema": 1, "confidence": True},
        {"schema": 1, "confidence": "0.5"},
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


@pytest.mark.parametrize("name", [WIRE_REQUEST_FILE, WIRE_RESPONSE_FILE])
def test_the_readme_and_schema_examples_are_generated_from_the_types(name: str) -> None:
    from pathlib import Path

    from web_router.routing.wire_schema import example_json

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert f"```json\n{example_json(name)}\n```" in readme, "paste wire_schema.example_json() into the README"
    assert json.loads(published(name))["examples"] == [json.loads(example_json(name))]

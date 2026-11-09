"""The router wire format, schema 1: a :class:`RouteRequest` as JSON, and a :class:`RouteDecision` read back.

Experimental. This is what the ``remote`` router sends a router service and
what it reads from one, and what any service written in another language
implements. Both sides carry ``"schema": 1``, the format's major version, and
the field names are the contract types' own. The JSON Schema files under
``web_router/data/schemas/router/v1/`` are generated from those types here, and
a test fails when they drift; regenerate them with
``uv run python -m web_router.routing.wire``.

Two rules keep the two sides working across versions, as they do in process: a
service ignores request fields it does not know, and the client ignores
decision fields it does not know. A decision of another major version is a
router error, and the call runs on the static order.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from web_router.constants_remote import (
    REMOTE_WHY_DECISION,
    REMOTE_WHY_NOT_JSON,
    REMOTE_WHY_SCHEMA,
    REMOTE_WHY_TOO_MANY,
    WIRE_CONFIDENCE_RANGE,
    WIRE_JSON_SCHEMA_DIALECT,
    WIRE_MAX_NAMES,
    WIRE_MAX_TEXT,
    WIRE_REQUEST_FILE,
    WIRE_REQUEST_TITLE,
    WIRE_RESPONSE_FILE,
    WIRE_RESPONSE_TITLE,
    WIRE_SCHEMA_DIR,
    WIRE_SCHEMA_KEY,
    WIRE_SCHEMA_VERSION,
)
from web_router.routing._runner import InvalidAnswer
from web_router.routing.contract import Budget, Candidate, RouteDecision, RouteRequest


class WireError(InvalidAnswer, ValueError):
    """A payload that does not fit the wire format: not an object, another major version, or ill-typed fields."""


@cache
def _request_adapter() -> TypeAdapter[RouteRequest]:
    return TypeAdapter(RouteRequest)


@cache
def _decision_adapter() -> TypeAdapter[RouteDecision]:
    return TypeAdapter(RouteDecision)


def _versioned(body: dict[str, Any]) -> dict[str, Any]:
    return {WIRE_SCHEMA_KEY: WIRE_SCHEMA_VERSION, **body}


def _payload(data: object) -> dict[str, Any]:
    """``data`` as an object of this major version, the version key and unset (``null``) fields left out.

    Raises:
        WireError: for anything but an object whose ``schema`` is this major version.
    """
    if not isinstance(data, Mapping):
        raise WireError(REMOTE_WHY_NOT_JSON)
    version = data.get(WIRE_SCHEMA_KEY)
    if isinstance(version, bool) or version != WIRE_SCHEMA_VERSION:
        raise WireError(REMOTE_WHY_SCHEMA.format(expected=WIRE_SCHEMA_VERSION))  # never echoes the service's value
    return {key: value for key, value in data.items() if key != WIRE_SCHEMA_KEY and value is not None}


def request_to_wire(req: RouteRequest) -> dict[str, Any]:
    """``req`` as the JSON object a router service receives, ``"schema": 1`` first."""
    return _versioned(_request_adapter().dump_python(req, mode="json"))


def request_from_wire(data: object) -> RouteRequest:
    """A router service's side: the :class:`RouteRequest` a JSON object carries, unknown fields ignored.

    Raises:
        WireError: for anything but a schema-1 request.
    """
    try:
        return _request_adapter().validate_json(json.dumps(_payload(data)), strict=True)
    except (ValidationError, ValueError) as exc:
        raise WireError(str(exc)) from None


def decision_to_wire(decision: RouteDecision) -> dict[str, Any]:
    """A router service's side: ``decision`` as the JSON object it answers, ``"schema": 1`` first."""
    return _versioned(_decision_adapter().dump_python(decision, mode="json"))


def decision_from_wire(data: object) -> RouteDecision:
    """The :class:`RouteDecision` a service's JSON answer carries; fields this version does not know are ignored.

    Raises:
        WireError: for anything but a schema-1 decision whose fields have the contract's types.
    """
    payload = _payload(data)
    try:
        # Strict, as JSON: a true is no confidence of 1.0, and "0.5" is no number.
        decision = _decision_adapter().validate_json(json.dumps(payload), strict=True)
    except ValidationError:
        raise WireError(REMOTE_WHY_DECISION) from None
    return _bounded(decision)


def _bounded(decision: RouteDecision) -> RouteDecision:
    """``decision`` within the wire's limits: its free text cut short, and refused when a field holds too much.

    A service's words reach the decision record and, through the MCP server, a model; neither needs an essay.

    Raises:
        WireError: for a list or mapping of more than :data:`WIRE_MAX_NAMES` entries.
    """
    sized = [decision.tools or (), decision.exclude, *(decision.providers or {}).values()]
    sized += list((decision.params or {}).values())
    if any(len(entry) > WIRE_MAX_NAMES for entry in sized):
        raise WireError(REMOTE_WHY_TOO_MANY.format(limit=WIRE_MAX_NAMES))
    return replace(
        decision,
        reason=None if decision.reason is None else decision.reason[:WIRE_MAX_TEXT],
        exclude={key: why[:WIRE_MAX_TEXT] for key, why in decision.exclude.items()},
    )


EXAMPLE_REQUEST = RouteRequest(
    prompt="read https://example.com/pricing",
    candidates=(
        Candidate(tool="extract", provider="jina", est_cost_usd=0.0, est_latency_s=2.1),
        Candidate(tool="search", provider="tavily", est_cost_usd=0.008, est_latency_s=1.2),
    ),
    deadline_s=1.0,
    urls=("https://example.com/pricing",),
    budget=Budget(max_cost_usd=0.05, time_limit_s=30.0),
)
"""The request the README and the schema file show, generated so neither can drift from the types."""

EXAMPLE_DECISION = RouteDecision(
    tools=["extract"],
    providers={"extract": ["jina"]},
    exclude={"search:tavily": "the prompt names a URL"},
    confidence=0.9,
    reason="the prompt names a URL",
)
"""The decision the README and the schema file show."""


def example_json(name: str) -> str:
    """The README's example for one schema file: its wire JSON, one field per line."""
    wire = request_to_wire(EXAMPLE_REQUEST) if name == WIRE_REQUEST_FILE else decision_to_wire(EXAMPLE_DECISION)
    return json.dumps(wire, indent=2)


def _schema(adapter: TypeAdapter[Any], title: str, mode: str) -> dict[str, Any]:
    generated = adapter.json_schema(mode=mode)  # type: ignore[arg-type]
    version = {"type": "integer", "const": WIRE_SCHEMA_VERSION, "description": "The wire format's major version."}
    return {
        "$schema": WIRE_JSON_SCHEMA_DIALECT,
        **generated,
        "title": title,
        "properties": {WIRE_SCHEMA_KEY: version, **generated["properties"]},
        "required": [WIRE_SCHEMA_KEY, *generated.get("required", ())],
    }


def request_schema() -> dict[str, Any]:
    """The JSON Schema of a request, generated from :class:`RouteRequest`."""
    return _schema(_request_adapter(), WIRE_REQUEST_TITLE, "serialization")


def response_schema() -> dict[str, Any]:
    """The JSON Schema of a decision, generated from :class:`RouteDecision`, with what the types cannot say.

    ``confidence`` is held to 0..1, as the merge rule holds it, and every field may be ``null`` -- unset, as
    :func:`decision_from_wire` reads it -- ``exclude`` included, whose type has no ``None``.
    """
    schema = _schema(_decision_adapter(), WIRE_RESPONSE_TITLE, "validation")
    fields = schema["properties"]
    for option in fields["confidence"]["anyOf"]:
        if option.get("type") == "number":
            option.update(WIRE_CONFIDENCE_RANGE)
    exclude = fields["exclude"]
    fields["exclude"] = {
        "anyOf": [{k: v for k, v in exclude.items() if k in ("type", "additionalProperties")}, {"type": "null"}],
        **{k: v for k, v in exclude.items() if k not in ("type", "additionalProperties")},
    }
    return schema


def rendered() -> dict[str, str]:
    """Each schema file's name and the text it should hold, its example included."""
    files = {WIRE_REQUEST_FILE: request_schema(), WIRE_RESPONSE_FILE: response_schema()}
    for name, schema in files.items():
        schema["examples"] = [json.loads(example_json(name))]
    return {name: json.dumps(schema, indent=2, sort_keys=False) + "\n" for name, schema in files.items()}


def published(name: str) -> str:
    """The text of one shipped schema file, as the wheel holds it."""
    folder = resources.files("web_router")
    for part in WIRE_SCHEMA_DIR:
        folder = folder / part
    return (folder / name).read_text(encoding="utf-8")


def main() -> None:
    """Write the schema files into the source tree, next to this package's other data."""
    target = Path(__file__).resolve().parents[1].joinpath(*WIRE_SCHEMA_DIR)
    target.mkdir(parents=True, exist_ok=True)
    for name, text in rendered().items():
        (target / name).write_text(text, encoding="utf-8")
        sys.stdout.write(f"wrote {target / name}\n")


if __name__ == "__main__":
    main()

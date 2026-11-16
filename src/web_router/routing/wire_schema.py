"""The router wire format's JSON Schema files and the examples they and the docs show, generated from the types.

Experimental. Both files under ``web_router/data/schemas/router/v1/`` come from
:class:`RouteRequest` and :class:`RouteDecision` through :mod:`.wire`'s own
adapters, with what the types cannot say added here; a test fails when the
shipped files drift. Regenerate them with::

    uv run python -m web_router.routing.wire_schema
"""

from __future__ import annotations

import json
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from web_router.constants_remote import (
    WIRE_CONFIDENCE_RANGE,
    WIRE_JSON_SCHEMA_DIALECT,
    WIRE_MAX_NAMES,
    WIRE_REQUEST_FILE,
    WIRE_REQUEST_TITLE,
    WIRE_RESPONSE_FILE,
    WIRE_RESPONSE_TITLE,
    WIRE_SCHEMA_DIR,
    WIRE_SCHEMA_KEY,
    WIRE_SCHEMA_VERSION,
)
from web_router.routing.contract import Budget, Candidate, RouteDecision, RouteRequest
from web_router.routing.wire import _decision_adapter, _request_adapter, decision_to_wire, request_to_wire

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
"""The request the docs and the schema file show, generated so neither can drift from the types."""

EXAMPLE_DECISION = RouteDecision(
    tools=["extract"],
    providers={"extract": ["jina"]},
    exclude={"search:tavily": "the prompt names a URL"},
    confidence=0.9,
    reason="the prompt names a URL",
)
"""The decision the docs and the schema file show."""


def example_json(name: str) -> str:
    """The docs' example for one schema file: its wire JSON, one field per line."""
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
    """The JSON Schema of a request, generated from :class:`RouteRequest`, its budget caps held to 0 or more.

    :class:`Budget` refuses a negative cap, so a request the schema passes is one
    :func:`~.wire.request_from_wire` reads.
    """
    schema = _schema(_request_adapter(), WIRE_REQUEST_TITLE, "serialization")
    for name in ("max_cost_usd", "time_limit_s"):
        for option in schema["$defs"]["Budget"]["properties"][name]["anyOf"]:
            if option.get("type") == "number":
                option["minimum"] = 0
    return schema


def _cap(node: Any) -> None:
    """Give every list and mapping under ``node`` the entry limit :func:`~.wire._bounded` holds a decision to."""
    if isinstance(node, dict):
        if node.get("type") == "array":
            node["maxItems"] = WIRE_MAX_NAMES
        elif node.get("type") == "object":
            node["maxProperties"] = WIRE_MAX_NAMES
        for value in node.values():
            _cap(value)
    elif isinstance(node, list):
        for value in node:
            _cap(value)


def response_schema() -> dict[str, Any]:
    """The JSON Schema of a decision, generated from :class:`RouteDecision`, with what the types cannot say.

    ``confidence`` is held to 0..1, as the merge rule holds it, every list and mapping to
    :data:`WIRE_MAX_NAMES` entries, as :func:`~.wire._bounded` holds them, an option's value in ``params``
    included, and every field may be ``null`` -- unset, as :func:`~.wire.decision_from_wire` reads it --
    ``exclude`` included, whose type has no ``None``.
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
    _cap(fields)
    # An option's value is any JSON; untyped, these cap it only when it is a list or a mapping, as _bounded does.
    fields["params"]["anyOf"][0]["additionalProperties"]["additionalProperties"] = {
        "maxItems": WIRE_MAX_NAMES,
        "maxProperties": WIRE_MAX_NAMES,
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

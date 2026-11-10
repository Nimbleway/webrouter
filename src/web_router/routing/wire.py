"""The router wire format, schema 1: a :class:`RouteRequest` as JSON, and a :class:`RouteDecision` read back.

Experimental. This is what the ``remote`` router sends a router service and
what it reads from one, and what any service written in another language
implements. Both sides carry ``"schema": 1``, the format's major version, and
the field names are the contract types' own. The JSON Schema files under
``web_router/data/schemas/router/v1/`` are generated from those types by
:mod:`.wire_schema`, and a test fails when they drift; regenerate them with
``uv run python -m web_router.routing.wire_schema``.

Two rules keep the two sides working across versions, as they do in process: a
service ignores request fields it does not know, and the client ignores
decision fields it does not know. A decision naming another major version is
a router error (:class:`WireVersionError`); one with no ``schema`` is an invalid
answer. Either way the call runs on the static order.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import cache
from typing import Any

from pydantic import TypeAdapter, ValidationError

from web_router.constants_remote import (
    REMOTE_WHY_DECISION,
    REMOTE_WHY_NOT_JSON,
    REMOTE_WHY_SCHEMA,
    REMOTE_WHY_TOO_MANY,
    REMOTE_WHY_VERSION,
    WIRE_MAX_NAME,
    WIRE_MAX_NAMES,
    WIRE_MAX_TEXT,
    WIRE_SCHEMA_KEY,
    WIRE_SCHEMA_VERSION,
)
from web_router.routing._runner import InvalidAnswer
from web_router.routing.contract import RouteDecision, RouteRequest


class WireError(InvalidAnswer, ValueError):
    """A payload that does not fit the wire format: not an object, no ``schema``, or ill-typed fields."""


class WireVersionError(ValueError):
    """A well-formed payload of another major version: a version mismatch, not an invalid answer."""


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
        WireError: for anything but an object whose ``schema`` is a number.
        WireVersionError: for a ``schema`` other than this major version.
    """
    if not isinstance(data, Mapping):
        raise WireError(REMOTE_WHY_NOT_JSON)
    version = data.get(WIRE_SCHEMA_KEY)  # neither message echoes the service's value
    if isinstance(version, bool) or not isinstance(version, int | float):
        raise WireError(REMOTE_WHY_SCHEMA)
    if version != WIRE_SCHEMA_VERSION:
        raise WireVersionError(REMOTE_WHY_VERSION.format(expected=WIRE_SCHEMA_VERSION))
    return {key: value for key, value in data.items() if key != WIRE_SCHEMA_KEY and value is not None}


def request_to_wire(req: RouteRequest) -> dict[str, Any]:
    """``req`` as the JSON object a router service receives, ``"schema": 1`` first."""
    return _versioned(_request_adapter().dump_python(req, mode="json"))


def request_from_wire(data: object) -> RouteRequest:
    """A router service's side: the :class:`RouteRequest` a JSON object carries, unknown fields ignored.

    Raises:
        WireError: for anything but a schema-1 request, another major version included.
    """
    try:
        return _request_adapter().validate_json(json.dumps(_payload(data)), strict=True)
    except (ValidationError, ValueError) as exc:  # a WireVersionError too: a service answers either with a 400
        raise WireError(str(exc)) from None


def decision_to_wire(decision: RouteDecision) -> dict[str, Any]:
    """A router service's side: ``decision`` as the JSON object it answers, ``"schema": 1`` first."""
    return _versioned(_decision_adapter().dump_python(decision, mode="json"))


def decision_from_wire(data: object) -> RouteDecision:
    """The :class:`RouteDecision` a service's JSON answer carries; fields this version does not know are ignored.

    Raises:
        WireError: for anything but a decision whose ``schema`` is a number and whose fields have the
            contract's types.
        WireVersionError: for a decision of another major version.
    """
    payload = _payload(data)
    try:
        # Strict, as JSON: a true is no confidence of 1.0, and "0.5" is no number.
        decision = _decision_adapter().validate_json(json.dumps(payload), strict=True)
    except ValidationError:
        raise WireError(REMOTE_WHY_DECISION) from None
    return _bounded(decision)


def _bounded(decision: RouteDecision) -> RouteDecision:
    """``decision`` within the wire's limits: its text and names cut short, and refused when a field holds too much.

    A service's words reach the decision record and, through the MCP server, a model; neither needs an essay.
    A name cut short names no candidate or option, so it is ignored, as any unknown one is.

    Raises:
        WireError: for a list or mapping of more than :data:`WIRE_MAX_NAMES` entries, a method's options and the
            lists and mappings they hold (one level down, such as extract's ``urls``) included.
    """
    options = list((decision.params or {}).values())
    held = [value for option in options for value in option.values() if isinstance(value, list | Mapping)]
    sized = [decision.tools or (), decision.exclude, *(decision.providers or {}).values(), *options, *held]
    if any(len(entry) > WIRE_MAX_NAMES for entry in sized):
        raise WireError(REMOTE_WHY_TOO_MANY.format(limit=WIRE_MAX_NAMES))
    if decision.confidence is not None and not 0 <= decision.confidence <= 1:
        raise WireError(REMOTE_WHY_DECISION)  # the response schema's range, held here too
    return replace(
        decision,
        reason=None if decision.reason is None else decision.reason[:WIRE_MAX_TEXT],
        exclude={key[:WIRE_MAX_NAME]: why[:WIRE_MAX_TEXT] for key, why in decision.exclude.items()},
        providers=_per_tool(decision.providers, lambda names: [name[:WIRE_MAX_NAME] for name in names]),
        params=_per_tool(decision.params, lambda option: {name[:WIRE_MAX_NAME]: v for name, v in option.items()}),
    )


def _per_tool(field: Mapping[Any, Any] | None, cut: Callable[[Any], Any]) -> Any:
    """``field``, a mapping per tool, with ``cut`` applied to each tool's value; ``None`` stays ``None``."""
    return None if field is None else {tool: cut(value) for tool, value in field.items()}

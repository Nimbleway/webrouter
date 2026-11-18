"""Oxylabs' ``OPTIONS`` parameter lines, read into a JSON Schema.

``OPTIONS`` on a Web API scrape endpoint answers with one entry per parameter.
A parameter is a line of bracketed facts, then its description::

    "[string] [required] [max_len: 8192]. The url of the page to scrape."

an array is that line inside a one-element list, and an object (``json``) is
a dict of its own parameters. The facts read here are the type, ``required``
or ``optional``, ``enum``, ``default``, ``min``/``max`` and
``min_len``/``max_len``, from
https://developers.oxylabs.io/products/web-api/scrape/endpoint-discovery,
read on 2026-11-10. A fact this does not know is left out of the schema; the
vendor's untouched answer stays in the description's ``raw``.
"""

from __future__ import annotations

from typing import Any

_TYPES = {
    "string": "string",
    "str": "string",
    "bool": "boolean",
    "boolean": "boolean",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "json": "object",
    "object": "object",
}

_BOUNDS = {
    "min": ("minimum", frozenset({"integer", "number"})),
    "max": ("maximum", frozenset({"integer", "number"})),
    "min_len": ("minLength", frozenset({"string"})),
    "max_len": ("maxLength", frozenset({"string"})),
}
"""Each bound, its JSON Schema keyword, and the types that keyword applies to.

A bound on any other type -- ``min_len`` on a ``[json]`` object, which Oxylabs
counts in characters of the serialized value -- has no JSON Schema keyword
that means it, so it is left out rather than written as one that is ignored.
"""

_ARRAY_OF = "array of "


def _closing(text: str) -> int | None:
    """Where the bracket opening ``text`` closes, counting nested ones: ``[array of [string]]`` is one fact."""
    depth = 0
    for at, char in enumerate(text):
        depth += (char == "[") - (char == "]")
        if depth == 0:
            return at
    return None


def _facts(line: str) -> tuple[list[str], str]:
    """The bracketed facts at the head of a line, and the description after them."""
    facts: list[str] = []
    rest = line.strip()
    while rest.startswith("[") and (close := _closing(rest)) is not None:
        facts.append(rest[1:close].strip())
        rest = rest[close + 1 :].lstrip()
    return facts, rest.lstrip(".").strip()


def _value(text: str, kind: str | None) -> Any:
    """A fact's value in the parameter's own type, when it reads as one; else the text as given."""
    text = text.strip()
    if kind == "boolean" and text.lower() in ("true", "false"):
        return text.lower() == "true"
    if kind in ("integer", "number"):
        try:
            return int(text) if kind == "integer" else float(text)
        except ValueError:
            return text
    return text


def _type_of(fact: str) -> dict[str, Any] | None:
    if fact.startswith(_ARRAY_OF):
        item = _TYPES.get(fact[len(_ARRAY_OF) :].strip("[] "))
        return {"type": "array", "items": {"type": item} if item else {}}
    kind = _TYPES.get(fact)
    return {"type": kind} if kind else None


def _bounded(bounds: dict[str, str], kind: Any) -> dict[str, Any]:
    """The JSON Schema keywords for the bounds that apply to a parameter of this type."""
    keywords: dict[str, Any] = {}
    for key, value in bounds.items():
        keyword, applies_to = _BOUNDS[key]
        if kind in applies_to:
            keywords[keyword] = _value(value, "number" if key in ("min", "max") else "integer")
    return keywords


def _line(line: str) -> tuple[dict[str, Any], bool]:
    """One parameter line as a schema, and whether it is required."""
    facts, description = _facts(line)
    schema: dict[str, Any] = {}
    bounds: dict[str, str] = {}
    required = False
    for fact in facts:
        key, sep, value = fact.partition(":")
        key = key.strip()
        if not sep:
            if key in ("required", "optional"):
                required = key == "required"
            elif "type" not in schema and (typed := _type_of(key)):
                schema.update(typed)
            continue
        kind = schema.get("type")
        if key == "enum":
            schema["enum"] = [_value(choice, kind) for choice in value.split(",") if choice.strip()]
        elif key == "default":
            schema["default"] = _value(value, kind)
        elif key in _BOUNDS:
            bounds[key] = value
    schema.update(_bounded(bounds, schema.get("type")))
    if description:
        schema["description"] = description
    return schema, required


def parameter(spec: Any) -> tuple[dict[str, Any], bool]:
    """One parameter, a line, a one-element list (an array of it) or a dict (an object), as a schema."""
    if isinstance(spec, str):
        return _line(spec)
    if isinstance(spec, list):
        item, required = parameter(spec[0]) if spec else ({}, False)
        array: dict[str, Any] = {"type": "array", "items": item}
        # The facts on the one element describe the whole array: its default is a list, its words are its own.
        if "default" in item:
            array["default"] = [item.pop("default")]
        if "description" in item:
            array["description"] = item.pop("description")
        return array, required
    if isinstance(spec, dict):
        return input_schema(spec), False
    return {}, False


def input_schema(spec: dict[str, Any], *, leave_out: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Every parameter of an ``OPTIONS`` answer as one object schema, but those in ``leave_out``."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, entry in spec.items():
        if name in leave_out:
            continue
        properties[name], needed = parameter(entry)
        if needed:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema

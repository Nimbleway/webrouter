"""An endpoint's Input table, from its params, and Output table, from its recorded example response."""

from __future__ import annotations

from typing import Any

from scripts.data_api_mdx import code, literal, text
from web_router.vendors.schema import VendorEndpoint, VendorParam

OUTPUT_DEPTH = 3
"""How deep the Output table follows an example response's nesting."""
OUTPUT_ROWS = 40


def _input_row(name: str, param: VendorParam) -> str:
    notes = [text(param.description)] if param.description else []
    if param.enum:
        notes.append(literal(" One of ") + literal(", ").join(code(choice) for choice in param.enum))
    if param.example is not None:
        notes.append(literal(" Example ") + code(param.example))
    required = f' <span className="wc-required">{literal("required")}</span>' if param.required else ""
    return (
        f"<tr><td>{code(name)}{required}</td><td>{literal(param.type)}</td>"
        f"<td>{literal(param.location)}</td><td>{''.join(notes)}</td></tr>"
    )


def input_panel(endpoint: VendorEndpoint) -> list[str]:
    head = f'<div className="wc-panel"><div className="wc-panel-title">{literal("Input")}</div>'
    if not endpoint.params:
        return [head, f'<p className="wc-none">{literal("No inputs.")}</p>', "</div>"]
    return [
        head,
        '<table className="wc-table"><thead>'
        "<tr>"
        + "".join(f"<th>{literal(name)}</th>" for name in ("Name", "Type", "In", "Notes"))
        + "</tr></thead><tbody>",
        *(_input_row(name, param) for name, param in endpoint.params.items()),
        "</tbody></table>",
        "</div>",
    ]


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _leaf_type(value: Any) -> str:
    if isinstance(value, list):
        first = next((item for item in value if item is not None), None)
        return f"{_type_name(first)}[]" if first is not None else "array"
    return _type_name(value)


def _descends(value: Any, depth: int) -> bool:
    return isinstance(value, (dict, list)) and depth < OUTPUT_DEPTH


def _list_fields(example: list[Any], prefix: str, depth: int) -> list[tuple[str, str]]:
    first = next((item for item in example if item is not None), None)
    rows = [(prefix, _leaf_type(example))] if prefix else []
    if _descends(first, depth):
        rows += output_fields(first, f"{prefix}[]", depth + 1)
    return rows


def _dict_fields(example: dict[str, Any], prefix: str, depth: int) -> list[tuple[str, str]]:
    rows = []
    for key, value in example.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if not _descends(value, depth):
            rows.append((path, _leaf_type(value)))
            continue
        if isinstance(value, dict):
            rows.append((path, "object"))
        rows += output_fields(value, path, depth + 1)
    return rows


def output_fields(example: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, str]]:
    """The fields an example response holds, as (path, type) -- ``items[].id`` for a list of objects."""
    if isinstance(example, list):
        return _list_fields(example, prefix, depth)
    if isinstance(example, dict):
        return _dict_fields(example, prefix, depth)
    return [(prefix or "(body)", _type_name(example))]


def output_panel(endpoint: VendorEndpoint) -> list[str]:
    if endpoint.example_response is None:
        return []
    fields = output_fields(endpoint.example_response)[:OUTPUT_ROWS]
    rows = [f"<tr><td>{code(path)}</td><td>{literal(kind)}</td></tr>" for path, kind in fields]
    return [
        f'<div className="wc-panel"><div className="wc-panel-title">{literal("Output")}</div>',
        f'<table className="wc-table"><thead><tr><th>{literal("Field")}</th><th>{literal("Type")}</th></tr>'
        "</thead><tbody>",
        *rows,
        "</tbody></table>",
        "</div>",
    ]

"""Shaping the vendor tools' results: a list of vendors or endpoints, one endpoint, one vendor's answer.

A vendor's answer is its own JSON, in its own shape, so there are no rows to
drop or fields to shorten the way a search result has: what does not fit is
cut as text, and the whole of it stays behind the handle for ``fetch_raw``.
Credentials never appear here -- the router adds them to the request and
masks them out of errors -- and the answer goes through the same redaction
as every other tool's, for the tokens a vendor might echo back.
"""

from __future__ import annotations

import json
from typing import Any

from web_router.constants import MCP_JSON_INDENT
from web_router.constants_vendors import MCP_MSG_VENDOR_TRUNCATED
from web_router.mcp.shaping import redact, redaction_note, render, truncate
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.credentials import CredentialSource, missing_credentials
from web_router.vendors.schema import Vendor, VendorEndpoint
from web_router.vendors.types import VendorResponse, VendorSummary


def _fit_rows(payload: dict[str, Any], key: str, budget: int, *, shed: tuple[str, ...], hint: str) -> dict[str, Any]:
    """Make a listing fit: drop the ``shed`` fields from every row first, then rows from the end, saying so."""
    if len(render(payload)) <= budget:
        return payload
    for row in payload[key]:
        for name in shed:
            row.pop(name, None)
    total = len(payload[key])
    left_out = f"{', '.join(shed)} left out" if shed else ""

    def note() -> str:
        shown = len(payload[key])
        parts = [part for part in (left_out, f"{total - shown} of {total} rows" if shown < total else "") if part]
        return f"Shortened to fit: {' and '.join(parts)}. {hint}"

    payload["note"] = note()
    while payload[key] and len(render(payload)) > budget:
        payload[key].pop()
        payload["note"] = note()
    return payload


def shape_vendor_list(rows: list[VendorSummary], *, budget: int) -> dict[str, Any]:
    """Every vendor in a line each: what it holds, whether it is usable here, and what to set when not.

    The catalog runs past a hundred vendors, so a listing that does not fit
    loses its summaries first and then its last rows, with a note saying how
    to narrow it.
    """
    payload = {
        "vendor_count": len(rows),
        "vendors": [
            {
                "vendor": row.vendor,
                "name": row.name,
                "category": row.category,
                "summary": row.summary,
                "endpoints": row.endpoint_count,
                "configured": row.configured,
                **({"set_to_enable": row.missing} if row.missing else {}),
            }
            for row in rows
        ],
    }
    hint = "Pass category, configured, or a query to narrow it."
    return _fit_rows(payload, "vendors", budget, shed=("summary",), hint=hint)


def shape_endpoint_list(
    endpoints: list[VendorEndpoint], catalog: VendorCatalog, credentials: CredentialSource, *, budget: int
) -> dict[str, Any]:
    """Matching endpoints, best first: the id to call, what it does, and whether its vendor is usable here."""
    ready: dict[str, list[str]] = {}
    rows = []
    for endpoint in endpoints:
        vendor = catalog.vendor(endpoint.vendor)
        if vendor.vendor not in ready:
            ready[vendor.vendor] = missing_credentials(vendor, credentials)
        row: dict[str, Any] = {
            "endpoint": endpoint.id,
            "name": endpoint.name,
            "kind": endpoint.kind,
            "vendor": vendor.name,
            "configured": not ready[vendor.vendor],
        }
        if ready[vendor.vendor]:
            row["set_to_enable"] = ready[vendor.vendor]
        rows.append(row)
    payload = {"endpoint_count": len(rows), "endpoints": rows}
    hint = "Pass a narrower query, a vendor, or a smaller limit."
    return _fit_rows(payload, "endpoints", budget, shed=(), hint=hint)


def shape_endpoint(
    endpoint: VendorEndpoint, vendor: Vendor, credentials: CredentialSource, *, budget: int
) -> dict[str, Any]:
    """One endpoint in full, with the vendor-wide notes and what its credentials are called.

    The inputs and notes are never cut: a model that calls with half the
    inputs described gets a refusal for the other half. A recorded answer is
    dropped whole when it does not fit, since half of one misleads.
    """
    params = {
        name: {
            key: value
            for key, value in {
                "in": spec.location,
                "type": spec.type,
                "required": spec.required or None,
                "description": spec.description or None,
                "enum": spec.enum,
                "example": spec.example,
            }.items()
            if value is not None
        }
        for name, spec in endpoint.params.items()
    }
    payload: dict[str, Any] = {
        "endpoint": endpoint.id,
        "name": endpoint.name,
        "summary": endpoint.summary,
        "kind": endpoint.kind,
        "method": endpoint.method,
        "path": endpoint.path,
        "host": vendor.base_for(endpoint.host),
        "params": params,
        "notes": [*endpoint.notes, *vendor.notes],
        "docs_url": endpoint.docs_url,
        "credentials": {name: spec.env for name, spec in vendor.auth.credentials.items()},
        "settings": {
            name: {"env": spec.env, "default": spec.default, "description": spec.description}
            for name, spec in vendor.settings.items()
        },
        "set_to_enable": missing_credentials(vendor, credentials),
    }
    if endpoint.example_response is not None:
        payload["example_response"] = endpoint.example_response
        if len(render(payload)) > budget:
            payload["example_response"] = None
    return payload


def shape_vendor_response(response: VendorResponse, handle: str, *, budget: int) -> dict[str, Any]:
    """The vendor's answer, whole when it fits, else cut as text with the handle that holds the rest."""
    data, count = redact(response.data if response.data is not None else response.text)
    payload: dict[str, Any] = {
        "vendor": response.vendor,
        "endpoint": response.endpoint,
        "status_code": response.status_code,
        "handle": handle,
        "truncated": False,
        "data": data,
    }
    note = redaction_note(count).strip()
    if len(render(payload)) > budget:
        room = max(budget - len(render({**payload, "data": ""})) - 200, 200)
        text = data if isinstance(data, str) else _json(data)
        payload["data"] = truncate(text, room)
        payload["truncated"] = True
        note = f"{note} {MCP_MSG_VENDOR_TRUNCATED.format(handle=handle)}".strip()
    if note:
        payload["note"] = note
    return payload


def _json(value: Any) -> str:
    return json.dumps(value, indent=MCP_JSON_INDENT, ensure_ascii=False, default=str)

"""The vendor tools' MCP constants: their names, input schemas, descriptions and messages.

Kept apart from :mod:`web_router.constants_vendors`, as
:mod:`web_router.constants_mcp` is from :mod:`web_router.constants`;
:mod:`web_router.constants` spreads the ``VENDOR_MCP_*`` tables into its own.
"""

from __future__ import annotations

from typing import Any, Final

from web_router.constants_vendors import VENDOR_CATEGORIES, VENDOR_ENV_ALLOW_WRITES

MCP_TOOL_VENDORS: Final = "vendors"
MCP_TOOL_VENDOR_ENDPOINT: Final = "vendor_endpoint"
MCP_TOOL_CALL_VENDOR: Final = "call_vendor"

MCP_VENDORS_PROPERTIES: dict[str, dict[str, Any]] = {
    "query": {
        "type": "string",
        "description": "What you want, in words: 'google ads spend by campaign', 'datadog error logs'. "
        "Lists matching endpoints, best first. Unset, lists the vendors instead.",
    },
    "vendor": {"type": "string", "description": "Only this vendor's endpoints (its slug, e.g. 'google-ads')."},
    "category": {"type": "string", "enum": list(VENDOR_CATEGORIES), "description": "Only vendors on this shelf."},
    "configured": {
        "type": "boolean",
        "description": "When listing vendors: true for the ones whose credentials are set here, false for the rest.",
    },
    "limit": {"type": "integer", "minimum": 1, "description": "Most endpoints to list; 20 unset."},
}

MCP_VENDOR_ENDPOINT_PROPERTIES: dict[str, dict[str, Any]] = {
    "endpoint": {"type": "string", "description": "The endpoint id, '<vendor>.<job>', as 'vendors' lists it."},
}

MCP_CALL_VENDOR_PROPERTIES: dict[str, dict[str, Any]] = {
    "endpoint": {
        "type": "string",
        "description": "A catalog endpoint id. Its inputs go in 'params'. Omit it, and give 'vendor', 'method' "
        "and 'path', to call a path the catalog does not name.",
    },
    "params": {
        "type": "object",
        "description": "The endpoint's own inputs, named as vendor_endpoint lists them. An undeclared one is refused.",
    },
    "vendor": {"type": "string", "description": "For a raw call: the vendor's slug."},
    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "For a raw call."},
    "path": {"type": "string", "description": "For a raw call: a path on the vendor's API, starting with '/'."},
    "query": {"type": "object", "description": "For a raw call: query parameters."},
    "host": {
        "type": "string",
        "description": "For a raw call: one of the vendor's other named hosts, if not its main one.",
    },
    "body": {"description": "For a raw call: the JSON body."},
    "allow_writes": {
        "type": "boolean",
        "description": "Required for a call that changes data on the vendor's side. Ask the user first, and send "
        "the vendor's dry-run option (validateOnly and the like) before the real call.",
    },
}

MCP_DESC_VENDORS = (
    "Find a vendor API to call with the user's own credentials: Google Ads, Datadog and the rest of the vendor "
    "catalog. With 'query', lists matching endpoints, best first, each with its id, what it does, whether it "
    "reads or writes, and whether its vendor's credentials are set here. Without, lists the vendors. Then "
    "'vendor_endpoint' for an endpoint's inputs and 'call_vendor' to run it."
)
MCP_DESC_VENDOR_ENDPOINT = (
    "Describe one vendor endpoint: every input with where it goes and an example, the notes a caller gets "
    "wrong without (units, paging, quirks), and the credentials it needs. Read it before 'call_vendor'."
)
MCP_DESC_CALL_VENDOR = (
    "Call a vendor's API with the user's credentials and return its answer as the vendor sent it: field names, "
    "units and paging are the vendor's own. Credentials are added here and never shown. Calls that change "
    "data are refused unless 'allow_writes' is true and the server allows writes. The full answer stays "
    "behind the returned handle for 'fetch_raw' when it does not fit."
)

MCP_MSG_VENDOR_ARG_TYPE: Final = "Tool {tool!r} takes {name!r} as {kind}, not {value!r}."
MSG_VENDOR_MCP_WRITE_REFUSED: Final = (
    "{target} changes data on {vendor}. This server refuses writes unless it was started with "
    + VENDOR_ENV_ALLOW_WRITES
    + "=1 and the call passes allow_writes=true. Ask the user before enabling either."
)
MCP_MSG_VENDOR_CALL_ARGS: Final = (
    "call_vendor needs either 'endpoint' (with 'params'), or 'vendor', 'method' and 'path' -- not both."
)
MCP_MSG_VENDOR_TRUNCATED: Final = "The answer was cut to fit; the whole of it is behind handle {handle!r} (fetch_raw)."
MCP_MSG_VENDOR_BINARY: Final = (
    "{size} bytes of {content_type}, not text; fetch_raw on handle {handle!r} returns them in content, URL-safe base64."
)

VENDOR_MCP_PROPERTIES: dict[str, dict[str, dict[str, Any]]] = {
    MCP_TOOL_VENDORS: MCP_VENDORS_PROPERTIES,
    MCP_TOOL_VENDOR_ENDPOINT: MCP_VENDOR_ENDPOINT_PROPERTIES,
    MCP_TOOL_CALL_VENDOR: MCP_CALL_VENDOR_PROPERTIES,
}
VENDOR_MCP_REQUIRED: dict[str, tuple[str, ...]] = {
    MCP_TOOL_VENDORS: (),
    MCP_TOOL_VENDOR_ENDPOINT: ("endpoint",),
    # `endpoint` or `vendor`+`method`+`path`: one-of, which a flat `required` cannot say; the tool checks it.
    MCP_TOOL_CALL_VENDOR: (),
}
VENDOR_MCP_DESCRIPTIONS: dict[str, str] = {
    MCP_TOOL_VENDORS: MCP_DESC_VENDORS,
    MCP_TOOL_VENDOR_ENDPOINT: MCP_DESC_VENDOR_ENDPOINT,
    MCP_TOOL_CALL_VENDOR: MCP_DESC_CALL_VENDOR,
}
"""The vendor tools' rows in :mod:`web_router.constants`' MCP tables, kept here so that file does not grow."""

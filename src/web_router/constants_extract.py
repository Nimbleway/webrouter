"""The MCP extract tool's schema and description.

Kept apart from :mod:`web_router.constants`, so that file stops growing; it
imports the extract tool's tables from here.
"""

from __future__ import annotations

from typing import Any, get_args

from web_router.constants_search import described
from web_router.types import ExtractFormat, ExtractRequest

# `profile` is left out on purpose, as on `browser_open`: a saved, logged-in
# identity is the user's own, set in code, never from a conversation.
MCP_EXTRACT_PROPERTIES: dict[str, dict[str, Any]] = {
    "urls": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": described(ExtractRequest, "urls"),
    },
    "format": {
        "type": "string",
        "enum": list(get_args(ExtractFormat)),
        "description": (
            "Which representation of each page to return. A backend that cannot produce it says so "
            "rather than returning a different one."
        ),
    },
    "data_apis": {
        "type": "boolean",
        "description": (
            "Also list, on each page under `data_apis`, the data APIs that serve that page's site: found locally "
            "at no cost, never called. On by default; false for the pages alone."
        ),
    },
    "configured_only": {
        "type": "boolean",
        "description": "List only the data APIs whose credentials are set here. Unset also lists the rest, "
        "each with the variable that would add it.",
    },
}

# A tool description is the routing decision: the model choosing between the tools is the actual router
# (`web_run` hands it to ours), and latency and cost are what it chooses on.
MCP_DESC_EXTRACT = (
    "Read pages you already have URLs for, as markdown, HTML or plain text, in about a second for a small "
    "batch. It reads pages, it does not find them: pass URLs from `search`, the user or a page you read. A "
    "page whose content needs a login, a form or clicks cannot be read this way; that is `browser_open`'s. "
    "provider='max' races every configured backend and returns the first answer, at one fetch per backend "
    "per URL: use it only when the user asked for speed over cost. A page whose site has its own data API "
    "lists it under `data_apis`, with whether its key is set: `call_vendor` runs it for structured data."
)

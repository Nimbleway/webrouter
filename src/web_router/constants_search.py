"""Search's shared option surface: the MCP search tool's schema and description, and its refusal messages.

Kept apart from :mod:`web_router.constants`, so that file stops growing; it
imports the search tool's tables, and :func:`described`, from here.
"""

from __future__ import annotations

from typing import Any, get_args

from pydantic import BaseModel

from web_router.types import SearchRequest
from web_router.types_options import Recency, ResearchEffort, SearchDepth

# Argument names are the library's own keyword names, so the MCP layer passes
# what it is given straight through rather than translating anything.


def described(model: type[BaseModel], field: str) -> str:
    """The request model's own description of ``field``, so the MCP schema says what the library's does."""
    description = model.model_fields[field].description
    if description is None:
        raise ValueError(f"{model.__name__}.{field} has no description for the MCP schema")
    return description


MCP_SEARCH_PROPERTIES: dict[str, dict[str, Any]] = {
    "query": {"type": "string", "description": "What to search for."},
    "max_results": {"type": "integer", "minimum": 1, "description": described(SearchRequest, "max_results")},
    "search_depth": {
        "type": "string",
        "enum": list(get_args(SearchDepth)),
        "description": described(SearchRequest, "search_depth"),
    },
    "include_domains": {
        "type": "array",
        "items": {"type": "string"},
        "description": described(SearchRequest, "include_domains"),
    },
    "exclude_domains": {
        "type": "array",
        "items": {"type": "string"},
        "description": described(SearchRequest, "exclude_domains"),
    },
    "country": {"type": "string", "description": described(SearchRequest, "country")},
    "locale": {"type": "string", "description": described(SearchRequest, "locale")},
    "recency": {"type": "string", "enum": list(get_args(Recency)), "description": described(SearchRequest, "recency")},
    "effort": {
        "type": "string",
        "enum": list(get_args(ResearchEffort)),
        "description": described(SearchRequest, "effort"),
    },
}

MCP_DESC_SEARCH = (
    "Search the web: ranked links with their titles, descriptions and page text. Returns in about "
    "a second, so call it freely and call it again with a better query. Use it to find pages you "
    "do not have URLs for; use `extract` when you already have the URL, and `research` only when "
    "the question needs an investigation rather than a list of results. `search_depth` sets how much "
    "text comes back per result; `effort` how hard the backend looks, and higher effort costs more and "
    "takes longer by a vendor-specific amount, so leave it unset unless needed. When the page that answers "
    "needs a login, a form or other interaction, suggest a browser (`browser_open`) to the user "
    "rather than only listing the steps."
)

MSG_UNSUPPORTED_DEPTH = (
    "It serves search_depth at {levels}. Ask for one of those, leave it unset for the backend's own default, or "
    "name a provider that serves the level -- see the search table in README.md."
)
"""Follows :data:`~web_router.constants.MSG_UNSUPPORTED_VALUES`: a shorter body is never served in a level's place."""

MSG_AUTO_REFUSES = "will not serve {values}"
"""Why ``auto`` passed over a member that maps the option but not the value it was set to."""

SITE_OPERATOR = "site:{domain}"
EXCLUDE_SITE_OPERATOR = "-site:{domain}"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
SITE_DOMAIN_PATTERN = rf"{_LABEL}(?:\.{_LABEL})+(?:/[A-Za-z0-9._~%/-]*)?"
"""A host name, optionally with a path: what a domain filter may hold when it goes into the query as ``site:``."""
MSG_NOT_A_DOMAIN = (
    "{value!r} is not a domain; a domain filter goes into the query as site:,"
    " so it must be a host name, optionally with a path."
)
"""A domain filter as the query operators Brave, WebIQ, DeepSeek and You.com MCP read (Brave excludes with ``NOT``)."""

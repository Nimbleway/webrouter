"""The model's tool input, checked against the tool's schema before anything is sent.

Everything here is the model's mistake to fix, so it comes back as an error
result naming what was wrong -- a wrong tool name, arguments that are not a
JSON object, a key the schema does not take -- and nothing is billed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from web_router.constants import INTEGRATION_EXTRACT_MAX_URLS
from web_router.integrations._messages import (
    GOT_AMONG,
    GOT_BLANK,
    GOT_COUNT,
    JSON_TYPES,
    LANGCHAIN_NAMES,
    MSG_LANGCHAIN_NAME,
    MSG_MISSING,
    MSG_NOT_JSON,
    MSG_NOT_OBJECT,
    MSG_QUERY,
    MSG_UNKNOWN_KEYS,
    MSG_UNKNOWN_TOOL,
    MSG_URLS,
    REFUSED_KIND,
)
from web_router.integrations._specs import TOOLS
from web_router.routing.merge_urls import UrlCheck


def unknown_tool(name: str, methods: Sequence[str]) -> str:
    """Why ``name`` did not run, when none of ``methods`` is a tool by that name; a LangChain name is pointed home."""
    for method, langchain_name in LANGCHAIN_NAMES.items():
        if name == langchain_name and method in methods:
            return MSG_LANGCHAIN_NAME.format(name=name, ours=TOOLS[method].name)
    return MSG_UNKNOWN_TOOL.format(names=", ".join(TOOLS[m].name for m in methods))


def checked(method: str, tool_input: object) -> tuple[dict[str, Any], str | None]:
    """The arguments as a dict, and why they break ``method``'s schema; ``None`` when they fit.

    A string is taken as the JSON text a vendor hands over and decoded first.
    """
    if isinstance(tool_input, str | bytes):
        try:
            tool_input = json.loads(tool_input)
        except ValueError as exc:
            return {}, MSG_NOT_JSON.format(error=exc)
    if not isinstance(tool_input, dict):
        return {}, MSG_NOT_OBJECT.format(got=_json_type(tool_input))
    field = next(iter(TOOLS[method].schema["properties"]))
    if extra := [key for key in tool_input if key != field]:
        return tool_input, MSG_UNKNOWN_KEYS.format(field=field, extra=", ".join(f"`{key}`" for key in extra))
    if field not in tool_input:
        return tool_input, MSG_MISSING.format(field=field)
    return tool_input, (_bad_urls if field == "urls" else _bad_query)(tool_input[field])


def _bad_query(query: object) -> str | None:
    if isinstance(query, str) and query.strip():
        return None
    return MSG_QUERY.format(got=GOT_BLANK if isinstance(query, str) else _json_type(query))


def _bad_urls(urls: object) -> str | None:
    if not isinstance(urls, list):
        return MSG_URLS.format(max_urls=INTEGRATION_EXTRACT_MAX_URLS, got=_json_type(urls))
    if not 1 <= len(urls) <= INTEGRATION_EXTRACT_MAX_URLS:
        return MSG_URLS.format(max_urls=INTEGRATION_EXTRACT_MAX_URLS, got=GOT_COUNT.format(count=len(urls)))
    if bad := [url for url in urls if not (isinstance(url, str) and url.strip())]:
        return MSG_URLS.format(max_urls=INTEGRATION_EXTRACT_MAX_URLS, got=GOT_AMONG.format(got=_json_type(bad[0])))
    return None


def _json_type(value: object) -> str:
    """``value``'s JSON type as a model would name it: ``null``, ``an array``, ``a string`` and so on."""
    if isinstance(value, str) and not value.strip():
        return JSON_TYPES["empty"]
    kind = next((name for name, types in _PYTHON_TYPES if isinstance(value, types)), "object")
    return JSON_TYPES[kind]


_PYTHON_TYPES: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("null", type(None)),
    ("boolean", bool),  # before number: a bool is an int
    ("number", (int, float)),
    ("string", str),
    ("array", list),
)


def screened(urls: list[str], check: UrlCheck) -> tuple[list[str], list[dict[str, Any]]]:
    """``urls`` the URL policy admits, in order; and each one it refused, as a failure row the model reads."""
    kept: list[str] = []
    refused: list[dict[str, Any]] = []
    for url in urls:
        if (why := check(url)) is None:
            kept.append(url)
        else:
            refused.append({"url": url, "reason": why, "kind": REFUSED_KIND, "retryable": False})
    return kept, refused

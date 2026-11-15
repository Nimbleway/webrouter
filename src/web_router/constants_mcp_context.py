"""What the MCP server costs a model's context: its tool allowlist, result rendering and output schemas.

Kept apart from :mod:`web_router.constants`, so that file stops growing. Every
tool the server lists, and every byte of its schema, is read on every turn of a
conversation; these are the knobs that keep that small.
"""

from __future__ import annotations

from typing import Any

from web_router.constants import (
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_BROWSER_CLOSE,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_SEARCH,
)

ENV_MCP_TOOLS = "WEB_ROUTER_MCP_TOOLS"
"""A comma-separated allowlist of the tools the server lists and serves, e.g. ``search,extract,answer``.

A name may end in ``*`` (``browser_*``). Unset or blank, every tool is served.
Read once, when the server starts, from the process environment only.
"""

MCP_TOOLS_SEPARATOR = ","

MCP_TOOL_PARTNERS: dict[str, str] = {
    MCP_TOOL_RESEARCH: MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_BROWSE: MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_BROWSER_OPEN: MCP_TOOL_BROWSER_CLOSE,
}
"""A tool the allowlist names brings its partner: a run that outlasts its wait needs polling, a browser closing."""

MCP_MSG_TOOLS_UNKNOWN = "%s names no tool this server has, so it was ignored: %s. Its tools: %s."
"""Logged at startup for each allowlist entry that matches nothing: a typo should not pass in silence."""

MCP_MSG_PLAN_NOT_SERVED = (
    "web_run planned {tool}, which {env_var} leaves off this server, so nothing was run. Call a tool it serves."
)
"""``web_run`` refusing to run a method the allowlist excludes: the allowlist is not bypassed by routing."""

MCP_JSON_SEPARATORS = (",", ":")
"""How a result is serialized: compact, since every space is a token the model pays for and never reads."""

MCP_EMPTY_VALUES: tuple[Any, ...] = ("", [])
"""Values a shaped row leaves out with ``None``: a key that says nothing costs as much as one that does."""

_STRING = {"type": "string"}
_HANDLE: dict[str, Any] = {"handle": _STRING, "truncated": {"type": "boolean"}, "note": _STRING}


def _rows(**fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "object", "properties": fields}}


def _result(**fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": {"provider": _STRING, **fields, **_HANDLE}, "required": ["handle"]}


MCP_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    MCP_TOOL_SEARCH: _result(answer=_STRING, results=_rows(url=_STRING, title=_STRING, content=_STRING)),
    MCP_TOOL_EXTRACT: _result(documents=_rows(url=_STRING, title=_STRING, content=_STRING)),
    MCP_TOOL_ANSWER: _result(answer=_STRING, citations=_rows(url=_STRING, marker=_STRING)),
}
"""The shape of the three core tools' structured results, kept to the fields a model reads.

A key with nothing in it is left out, so only ``handle`` is required.
"""

MCP_ELAPSED_DIGITS = 2
"""Decimal places a shaped result keeps of ``elapsed_s``: hundredths of a second is all a reader acts on."""

MCP_COST_DIGITS = 6
"""Decimal places a shaped result keeps of ``cost_usd``: a millionth of a dollar, past any per-call price."""

MCP_MSG_ERROR_FACTS = " (kind={kind}, retryable={retryable})"
"""Appended to a failed tool call's text when the error says what kind it was, so a model branches on fields."""

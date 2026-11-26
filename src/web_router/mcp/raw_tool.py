"""The ``fetch_raw`` tool: a result's whole payload, by the handle the shaped result carried.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router.constants import MCP_ARG_MAX_CHARS, MCP_BROWSER_TOOLS, MCP_TOOL_FETCH_RAW
from web_router.constants_mcp_context import (
    MCP_ARG_OFFSET,
    MCP_MSG_NO_RAW,
    MCP_MSG_RAW_BAD_PAGE,
    MCP_MSG_RAW_NEXT_PAGE,
    MCP_MSG_RAW_PAGE,
    MCP_MSG_RAW_PAST_END,
)
from web_router.mcp.masking import mask_configured
from web_router.mcp.shaping import max_response_chars, redact, redaction_note
from web_router.mcp.shaping_fit import render
from web_router.providers.base import BrowseJob

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


async def fetch_raw_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The backend's payload with every configured credential's value masked; a browse or browser one redacted too.

    With ``offset`` or ``max_chars``, one page of the payload's JSON instead of the whole of it.
    """
    record = tools.record(MCP_TOOL_FETCH_RAW, arguments)
    if record.raw is None:
        raise ValueError(MCP_MSG_NO_RAW.format(handle=record.handle))
    offset = _page_argument(arguments, MCP_ARG_OFFSET, minimum=0)
    max_chars = _page_argument(arguments, MCP_ARG_MAX_CHARS, minimum=1)
    if not isinstance(record.job, BrowseJob) and record.tool not in MCP_BROWSER_TOOLS:
        # Deliberately unbounded unless a page is asked for: this tool exists to hand back exactly what
        # the shaped result could not carry.
        payload: dict[str, Any] = {"handle": record.handle, "tool": record.tool, "raw": mask_configured(record.raw)}
    else:
        # A browse payload is the one that is not handed back verbatim: it can
        # carry the session the agent ran in, and a live view or a key-bearing
        # CDP URL is control of a billed browser, not data about the task.
        raw, count = redact(record.raw)
        raw = mask_configured(raw)  # after redaction, so a masked short key cannot hide a URL from it
        payload = {"handle": record.handle, "tool": record.tool, "raw": raw}
        if count:
            payload["note"] = redaction_note(count).strip()
    if offset is None and max_chars is None:
        return payload
    return _page(payload, offset or 0, max_chars or max_response_chars())


def _page_argument(arguments: Mapping[str, Any], name: str, *, minimum: int) -> int | None:
    value: object = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(MCP_MSG_RAW_BAD_PAGE.format(name=name, minimum=minimum, value=value))
    return value


def _page(payload: dict[str, Any], offset: int, max_chars: int) -> dict[str, Any]:
    """One page of ``payload``'s raw JSON, from ``offset``, and where the next one starts.

    A client that shortens a long tool result keeps its start and end and drops the middle: Codex as shipped keeps
    about 10 KiB, while a finished research run's payload is two to three times its report, 40,000 characters and
    more. The whole payload in one result reached such a model with its middle gone; pages of it reach it whole, a
    page per call, and a model stops once it has what it needs. Masking and redaction ran on the whole payload
    first, so a page boundary cannot split a credential or a session URL out of their reach.
    """
    handle = str(payload["handle"])
    text = render(payload["raw"])
    total = len(text)
    if offset >= total:
        raise ValueError(MCP_MSG_RAW_PAST_END.format(offset=offset, handle=handle, total=total))
    end = min(offset + max_chars, total)
    note = MCP_MSG_RAW_PAGE.format(start=offset, end=end, total=total)
    if end < total:
        note += MCP_MSG_RAW_NEXT_PAGE.format(handle=handle, next_offset=end)
    if payload.get("note"):
        note = f"{payload['note']} {note}"
    paged: dict[str, Any] = {"handle": handle, "tool": payload["tool"], "page": text[offset:end], "offset": offset}
    if end < total:
        paged["next_offset"] = end
    return {**paged, "total_chars": total, "note": note}

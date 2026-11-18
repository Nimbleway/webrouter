"""The ``fetch_raw`` tool: a result's whole payload, by the handle the shaped result carried.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router.constants import MCP_BROWSER_TOOLS, MCP_MSG_NO_RAW, MCP_TOOL_FETCH_RAW
from web_router.mcp.masking import mask_configured
from web_router.mcp.shaping import redact, redaction_note
from web_router.providers.base import BrowseJob

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


async def fetch_raw_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The backend's payload with every configured credential's value masked; a browse or browser one redacted too."""
    record = tools.record(MCP_TOOL_FETCH_RAW, arguments)
    if record.raw is None:
        raise ValueError(MCP_MSG_NO_RAW.format(handle=record.handle))
    if not isinstance(record.job, BrowseJob) and record.tool not in MCP_BROWSER_TOOLS:
        # Deliberately unbounded: this tool exists to hand back exactly what
        # the shaped result could not carry.
        return {"handle": record.handle, "tool": record.tool, "raw": mask_configured(record.raw)}
    # A browse payload is the one that is not handed back verbatim: it can
    # carry the session the agent ran in, and a live view or a key-bearing
    # CDP URL is control of a billed browser, not data about the task.
    raw, count = redact(record.raw)
    raw = mask_configured(raw)  # after redaction, so a masked short key cannot hide a URL from it
    payload: dict[str, Any] = {"handle": record.handle, "tool": record.tool, "raw": raw}
    if count:
        payload["note"] = redaction_note(count).strip()
    return payload

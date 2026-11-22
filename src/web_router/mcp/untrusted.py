"""The untrusted mark on results that carry web or backend text the shapers did not mark.

Shaped results are marked as they are fitted (:mod:`web_router.mcp.shaping_fit`);
``fetch_raw``, ``call_vendor`` and the browser actions that show a page get the
mark here, at the one exit every tool result passes through.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from web_router.constants_mcp_instructions import MCP_UNTRUSTED_MARK, MCP_UNTRUSTED_TOOLS
from web_router.mcp.shaping import ToolOutput


def tool_output(name: str, payload: dict[str, Any] | ToolOutput) -> ToolOutput:
    """``payload`` as a :class:`ToolOutput`, with the mark as its first key when ``name`` shows web text.

    First key of the first block, so it is read before the page text, the snapshot tree or the image after it.
    A payload already marked keeps its other keys where they were.
    """
    output = payload if isinstance(payload, ToolOutput) else ToolOutput(payload=payload)
    if name not in MCP_UNTRUSTED_TOOLS:
        return output
    return dataclasses.replace(output, payload={**MCP_UNTRUSTED_MARK, **output.payload})

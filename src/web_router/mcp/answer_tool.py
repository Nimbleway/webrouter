"""The ``answer`` tool: one question, answered, with the sources the answer cites.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit. A thin
projection of :meth:`~web_router.AsyncWebRouter.answer`, plus the two arguments
only this layer reads -- whether to list the sources the answer did not cite,
and whether to show each source's quoted passage -- which never reach the library.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router.constants import MCP_TOOL_ANSWER
from web_router.constants_answer import MCP_ARG_ALL_SOURCES, MCP_ARG_SNIPPETS
from web_router.mcp.shaping import max_response_chars
from web_router.mcp.shaping_answer import shape_answer

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


async def answer_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The backend's answer and its cited sources, cut to fit; the whole payload stays behind the handle."""
    kwargs = tools.arguments(MCP_TOOL_ANSWER, arguments)
    all_sources, snippets = bool(kwargs.pop(MCP_ARG_ALL_SOURCES, False)), bool(kwargs.pop(MCP_ARG_SNIPPETS, False))
    response = await tools.router.answer(**kwargs)
    record = tools.store.add(MCP_TOOL_ANSWER, raw=response.raw)
    return shape_answer(
        response, record.handle, budget=max_response_chars(), all_sources=all_sources, snippets=snippets
    )

"""Exa provider over its hosted MCP server, which needs no credential.

A different route from :mod:`web_router.providers.exa`, not a mode of it: Exa's
REST ``/search`` answers 402 without a key (a request there is paid per call in
USDC), while ``mcp.exa.ai`` is documented as free and rate-limited without one.

The tool answers in text blocks -- ``Title:``, ``URL:``, ``Published:``,
``Author:`` and then ``Highlights:`` running to the next block -- rather than
JSON. :meth:`ExaMcpProvider._unwrap` reads them into the shape Exa's REST
payload has, so normalization is :class:`ExaProvider`'s and a value the text
does not state (``N/A``) stays unset.

Install with the ``search`` extra -- it needs ``httpx`` and
nothing else.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from web_router.constants import (
    EXA_MCP_BASE_URL,
    EXA_MCP_FIELDS,
    EXA_MCP_HIGHLIGHTS_LABEL,
    EXA_MCP_PARAM_NAMES,
    EXA_MCP_RESULT_START,
    EXA_MCP_SEARCH_PATH,
    EXA_MCP_SEARCH_TOOL,
    EXA_MCP_UNSET,
    MSG_MCP_NO_RESULT,
    PROVIDER_EXA_MCP,
)
from web_router.exceptions import ProviderError
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._utils import build_payload
from web_router.providers.exa import ExaProvider
from web_router.types import SearchRequest

# A block starts at a Title line followed by a URL line, so a highlight that
# happens to begin with "Title: " does not split its result in two.
_BLOCK_START = re.compile(rf"(?m)^(?={re.escape(EXA_MCP_RESULT_START)}.*\nURL: )")


class ExaMcpProvider(McpSearchMixin, ExaProvider):
    """Routes searches to Exa's anonymous MCP server."""

    name: ClassVar[str] = PROVIDER_EXA_MCP
    supported_parameters: ClassVar[frozenset[str]] = frozenset(EXA_MCP_PARAM_NAMES)
    mcp_tool: ClassVar[str] = EXA_MCP_SEARCH_TOOL

    base_url: ClassVar[str] = EXA_MCP_BASE_URL
    search_path: ClassVar[str] = EXA_MCP_SEARCH_PATH
    env_api_key: ClassVar[str | None] = None

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # The tool requires an objective beside the query; with one normalized
        # query, both are that query, as Parallel's mapping does.
        return build_payload(request, EXA_MCP_PARAM_NAMES, query=request.query, objective=request.query)

    def _unwrap(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        # Only a block with a URL is a result: a notice or a "no results" line
        # around them is not, and a reply of nothing else is a failure, so auto
        # moves to the next member rather than serving an empty answer.
        hits = [hit for hit in (self._hit(block) for block in _BLOCK_START.split(text)) if hit.get("url")]
        if not hits:
            raise ProviderError(self.name, MSG_MCP_NO_RESULT)
        return {"results": hits}

    @staticmethod
    def _hit(block: str) -> dict[str, Any]:
        hit: dict[str, Any] = {}
        lines = block.strip("\n").split("\n")
        for position, line in enumerate(lines):
            if line.rstrip() == f"{EXA_MCP_HIGHLIGHTS_LABEL}:":
                highlights = "\n".join(lines[position + 1 :]).strip()
                if highlights:
                    hit["highlights"] = [highlights]
                break
            label, _, value = line.partition(": ")
            if label in EXA_MCP_FIELDS and value.strip() and value.strip() != EXA_MCP_UNSET:
                hit[EXA_MCP_FIELDS[label]] = value.strip()
        return hit

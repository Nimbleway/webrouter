"""You.com provider over its MCP endpoint, which needs no credential.

This is a different service from :mod:`web_router.providers.you`, not a mode of
it: the REST search API at ``ydc-index.io`` is paid and returns 402 without a
key, while ``api.you.com/mcp?profile=free`` is open. The transport differs too
-- JSON-RPC wrapped in server-sent events rather than a plain JSON body, carried
by :class:`~web_router.providers._mcp.McpSearchMixin`.

What it returns, once unwrapped, is the same payload shape the REST API sends,
so this subclasses :class:`YouProvider` for normalization and overrides only
the transport. A change to You.com's result shape then lands in both at once.

Install with the ``search`` extra -- it needs ``httpx`` and
nothing else.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    PROVIDER_YOU_MCP,
    YOU_MCP_BASE_URL,
    YOU_MCP_EXTRACTION_BY_DEPTH,
    YOU_MCP_SEARCH_PATH,
    YOU_MCP_SEARCH_TOOL,
    YOU_PARAM_NAMES,
)
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._utils import build_payload
from web_router.providers.you import YouProvider
from web_router.types import SearchRequest


class YouMcpProvider(McpSearchMixin, YouProvider):
    """Routes searches to You.com's anonymous MCP endpoint."""

    name: ClassVar[str] = PROVIDER_YOU_MCP
    mcp_tool: ClassVar[str] = YOU_MCP_SEARCH_TOOL

    base_url: ClassVar[str] = YOU_MCP_BASE_URL
    search_path: ClassVar[str] = YOU_MCP_SEARCH_PATH
    env_api_key: ClassVar[str | None] = None

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        """Same arguments as the REST provider, except how extraction is spelled."""
        extraction = YOU_MCP_EXTRACTION_BY_DEPTH.get(request.search_depth) if request.search_depth else None
        return build_payload(request, YOU_PARAM_NAMES, query=request.query, extraction=extraction)

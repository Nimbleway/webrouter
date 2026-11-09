"""Exa provider over its hosted MCP server, which needs no credential.

A different route from :mod:`web_router.providers.exa`, not a mode of it: Exa's
REST ``/search`` answers 402 without a key (a request there is paid per call in
USDC), while ``mcp.exa.ai`` is documented as free and rate-limited without one.

It calls ``web_search_advanced_exa``, which the server serves when its URL names
it in ``?tools=``, rather than the default ``web_search_exa``: the advanced tool
takes the domain filters, a location, a published-date start, content sizes and
a search type, so every shared option but ``locale`` maps. It answers in Exa's
REST JSON, so normalization is :class:`ExaProvider`'s.

Install with the ``search`` extra -- it needs ``httpx`` and
nothing else.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from web_router.constants import MSG_MCP_NO_RESULT, PROVIDER_EXA_MCP
from web_router.constants_exa import (
    EXA_MCP_BASE_URL,
    EXA_MCP_CONTENTS,
    EXA_MCP_CONTENTS_BY_DEPTH,
    EXA_MCP_DATE_FORMAT,
    EXA_MCP_PARAM_NAMES,
    EXA_MCP_SEARCH_PATH,
    EXA_MCP_SEARCH_TOOL,
    EXA_MCP_TOOLS_PARAM,
    EXA_MCP_TYPE_BY_EFFORT,
    EXA_RECENCY_FIELD,
    EXA_TYPE_FIELD,
)
from web_router.exceptions import ProviderError
from web_router.providers._clock import recency_start
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._utils import build_payload, mapped
from web_router.providers.exa import ExaProvider
from web_router.types import SearchRequest


class ExaMcpProvider(McpSearchMixin, ExaProvider):
    """Routes searches to Exa's anonymous MCP server."""

    name: ClassVar[str] = PROVIDER_EXA_MCP
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {*EXA_MCP_PARAM_NAMES, "recency", "search_depth", "effort"}
    )
    mcp_tool: ClassVar[str] = EXA_MCP_SEARCH_TOOL

    base_url: ClassVar[str] = EXA_MCP_BASE_URL
    search_path: ClassVar[str] = EXA_MCP_SEARCH_PATH
    env_api_key: ClassVar[str | None] = None

    def _call_kwargs(self, request: SearchRequest) -> dict[str, Any]:
        return {**super()._call_kwargs(request), "params": {EXA_MCP_TOOLS_PARAM: self.mcp_tool}}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        since = recency_start(request.recency)
        contents = EXA_MCP_CONTENTS_BY_DEPTH[request.search_depth] if request.search_depth else EXA_MCP_CONTENTS
        return build_payload(
            request,
            EXA_MCP_PARAM_NAMES,
            query=request.query,
            **contents,
            **{
                EXA_RECENCY_FIELD: since.strftime(EXA_MCP_DATE_FORMAT) if since else None,
                EXA_TYPE_FIELD: mapped(EXA_MCP_TYPE_BY_EFFORT, request.effort),
            },
        )

    def _unwrap(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        # A reply with no result -- a notice in place of the JSON, or an empty
        # list -- is a failure, so auto moves to the next member rather than
        # serving an empty answer.
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError as exc:
            raise ProviderError(self.name, MSG_MCP_NO_RESULT, original=exc) from exc
        if not isinstance(payload, dict) or not any(
            isinstance(hit, dict) and hit.get("url") for hit in payload.get("results") or []
        ):
            raise ProviderError(self.name, MSG_MCP_NO_RESULT)
        return payload

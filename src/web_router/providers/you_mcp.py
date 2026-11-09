"""You.com provider over its MCP endpoint, which needs no credential.

This is a different service from :mod:`web_router.providers.you`, not a mode of
it: the REST search API at ``ydc-index.io`` is paid and returns 402 without a
key, while ``api.you.com/mcp?profile=free`` is open. The transport differs too
-- JSON-RPC wrapped in server-sent events rather than a plain JSON body, carried
by :class:`~web_router.providers._mcp.McpSearchMixin`.

What it returns, once unwrapped, is the same payload shape the REST API sends,
so this subclasses :class:`YouProvider` for normalization, and for the options
it accepts and the values it refuses, and overrides the transport and how the
request is spelled. A change to You.com's result shape then lands in both at once.

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
)
from web_router.constants_search import MSG_SITE_DOMAINS
from web_router.constants_you import (
    YOU_FRESHNESS_FIELD,
    YOU_MCP_COUNTRY,
    YOU_MCP_LANGUAGE,
    YOU_MCP_PARAM_NAMES,
    YOU_RECENCY,
)
from web_router.providers._language import language_tag
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._search_text import site_operators, unsited_domains
from web_router.providers._utils import build_payload, mapped
from web_router.providers.you import YouProvider
from web_router.types import SearchRequest


class YouMcpProvider(McpSearchMixin, YouProvider):
    """Routes searches to You.com's anonymous MCP endpoint.

    It maps what the REST provider maps and refuses the same values: ``country``,
    ``locale`` and ``include_domains`` travel in the query as ``loc:``, ``lang:``
    and ``site:``, which the tool takes out and applies, and the rest are its
    arguments.
    """

    name: ClassVar[str] = PROVIDER_YOU_MCP
    mcp_tool: ClassVar[str] = YOU_MCP_SEARCH_TOOL
    # Only ``include_domains`` goes in as ``site:``; with both lists set, ``exclude_domains`` gives the REST reason.
    refusal_reasons: ClassVar[dict[str, str]] = {**YouProvider.refusal_reasons, "include_domains": MSG_SITE_DOMAINS}

    base_url: ClassVar[str] = YOU_MCP_BASE_URL
    search_path: ClassVar[str] = YOU_MCP_SEARCH_PATH
    env_api_key: ClassVar[str | None] = None

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        """The REST API's refusals, and an ``include_domains`` that cannot go into the query as ``site:``."""
        return {**super().refused_options(request), **unsited_domains(request, ("include_domains",))}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        extraction = mapped(YOU_MCP_EXTRACTION_BY_DEPTH, request.search_depth)
        return build_payload(
            request,
            YOU_MCP_PARAM_NAMES,
            query=self._query(request),
            extraction=extraction,
            **{YOU_FRESHNESS_FIELD: mapped(YOU_RECENCY, request.recency)},
        )

    @staticmethod
    def _query(request: SearchRequest) -> str:
        operators = site_operators(request.include_domains, joiner=" ")
        if request.locale and (language := language_tag(request.locale)):
            operators.append(YOU_MCP_LANGUAGE.format(language=language))
        if request.country:
            operators.append(YOU_MCP_COUNTRY.format(country=request.country.upper()))
        return " ".join([request.query, *operators])

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

import json
from typing import Any, ClassVar

from web_router.constants import (
    MSG_MCP_NO_RESULT,
    PROVIDER_YOU_MCP,
    YOU_MCP_BASE_URL,
    YOU_MCP_EXTRACTION_BY_DEPTH,
    YOU_MCP_SEARCH_PATH,
    YOU_MCP_SEARCH_TOOL,
)
from web_router.constants_you import (
    MSG_YOU_MCP_EXCLUDE_DOMAINS,
    MSG_YOU_MCP_SITE_HOST,
    YOU_FRESHNESS_FIELD,
    YOU_MCP_COUNTRY,
    YOU_MCP_LANGUAGE,
    YOU_MCP_PARAM_NAMES,
    YOU_RECENCY,
)
from web_router.exceptions import ProviderError
from web_router.providers._language import language_tag
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._search_text import DOMAIN_LISTS, site_operators, unsited_domains
from web_router.providers._utils import build_payload, mapped
from web_router.providers.you import YouProvider
from web_router.types import SearchRequest


def _has_path(domain: str) -> bool:
    """Whether a domain filter names a path past its host: a scheme or a trailing slash alone does not."""
    host_and_path = domain.split("://", 1)[-1]
    return "/" in host_and_path.rstrip("/")


class YouMcpProvider(McpSearchMixin, YouProvider):
    """Routes searches to You.com's anonymous MCP endpoint.

    It maps what the REST provider maps and refuses the same values: ``country``,
    ``locale`` and ``include_domains`` travel in the query as ``loc:``, ``lang:``
    and ``site:``, which the tool takes out and applies, and the rest are its
    arguments.
    """

    name: ClassVar[str] = PROVIDER_YOU_MCP
    mcp_tool: ClassVar[str] = YOU_MCP_SEARCH_TOOL
    # You.com matches either list on the host alone, so a path in one is refused; with both set, exclude says so.
    refusal_reasons: ClassVar[dict[str, str]] = {
        **YouProvider.refusal_reasons,
        "include_domains": MSG_YOU_MCP_SITE_HOST,
        "exclude_domains": MSG_YOU_MCP_EXCLUDE_DOMAINS,
    }

    base_url: ClassVar[str] = YOU_MCP_BASE_URL
    search_path: ClassVar[str] = YOU_MCP_SEARCH_PATH
    env_api_key: ClassVar[str | None] = None

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        """The REST API's refusals, an ``include_domains`` that cannot go in as ``site:``, and a domain with a path."""
        lists = {name: getattr(request, name) or [] for name in DOMAIN_LISTS}
        with_path = {name: domains for name, domains in lists.items() if any(map(_has_path, domains))}
        return {**super().refused_options(request), **unsited_domains(request, ("include_domains",)), **with_path}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        extraction = mapped(YOU_MCP_EXTRACTION_BY_DEPTH, request.search_depth)
        return build_payload(
            request,
            YOU_MCP_PARAM_NAMES,
            query=self._query(request),
            extraction=extraction,
            **{YOU_FRESHNESS_FIELD: mapped(YOU_RECENCY, request.recency)},
        )

    def _unwrap(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        # A search with no hits answers the JSON object and then a plain-text
        # hint ("No results for ... Issue a new query"), so the text as a whole
        # is not JSON. Read the object and leave the hint.
        try:
            payload, _ = json.JSONDecoder().raw_decode(text.lstrip()) if text else (None, 0)
        except json.JSONDecodeError as exc:
            raise ProviderError(self.name, MSG_MCP_NO_RESULT, original=exc) from exc
        if not isinstance(payload, dict):
            raise ProviderError(self.name, MSG_MCP_NO_RESULT)
        return payload

    @staticmethod
    def _query(request: SearchRequest) -> str:
        operators = site_operators(request.include_domains, joiner=" ")
        if request.locale and (language := language_tag(request.locale)):
            operators.append(YOU_MCP_LANGUAGE.format(language=language))
        if request.country:
            operators.append(YOU_MCP_COUNTRY.format(country=request.country.upper()))
        return " ".join([request.query, *operators])

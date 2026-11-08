"""Parallel provider over its hosted Search MCP, which needs no credential.

A different route from :mod:`web_router.providers.parallel`: Parallel's REST
search answers 401 without a key, while ``search.parallel.ai/mcp`` is documented
as free without one. Its ``web_search`` tool returns the REST ``/v1/search``
payload as JSON text, so normalization is :class:`ParallelProvider`'s.

It maps no option but ``max_results``. Its mode and source settings are set
only through a connection header that needs an API key, which this keyless
route does not send; the tool's own arguments are the queries, a session id
and the caller's model name. Its schema allows ``site:`` with a bare domain
inside a query, which steers the search but is not documented as a filter, so
``include_domains`` stays refused rather than mapped onto it.

The tool takes no result count -- asked for 3 it returned 10, checked live -- so
``max_results`` is honoured here by keeping the first that many results, in the
order Parallel ranked them; ``raw`` keeps every one.

Install with the ``search`` extra -- it needs ``httpx`` and
nothing else.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    PARALLEL_MCP_BASE_URL,
    PARALLEL_MCP_SEARCH_PATH,
    PARALLEL_MCP_SEARCH_TOOL,
    PROVIDER_PARALLEL_MCP,
)
from web_router.providers._mcp import McpSearchMixin
from web_router.providers._utils import build_payload
from web_router.providers.parallel import ParallelProvider
from web_router.types import SearchRequest, SearchResponse


class ParallelMcpProvider(McpSearchMixin, ParallelProvider):
    """Routes searches to Parallel's anonymous Search MCP."""

    name: ClassVar[str] = PROVIDER_PARALLEL_MCP
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"max_results"})
    mcp_tool: ClassVar[str] = PARALLEL_MCP_SEARCH_TOOL

    base_url: ClassVar[str] = PARALLEL_MCP_BASE_URL
    search_path: ClassVar[str] = PARALLEL_MCP_SEARCH_PATH
    env_api_key: ClassVar[str | None] = None

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        # max_results is not sent: the tool ignores it, and sending it would
        # suggest the server applied it. It is applied to the response instead.
        return build_payload(request, {}, objective=request.query, search_queries=[request.query])

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        response = super()._to_response(request, payload)
        if request.max_results is None:
            return response
        return response.model_copy(update={"results": response.results[: request.max_results]})

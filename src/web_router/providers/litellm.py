"""Exa, Tavily and Parallel search through a LiteLLM proxy's Search API.

A different route to each vendor, not a mode of it, as ``exa-mcp`` is: the call goes to the proxy's
``POST /v1/search/{search_tool_name}`` with the caller's LiteLLM key, and the proxy holds the vendor's key and bills
the call to the LiteLLM key's budget. The proxy forwards the vendor's own request fields as they are, so each
provider here sends exactly what the vendor's own provider would (every shared option, ``effort`` included) plus
LiteLLM's ``query`` and ``max_results``, and reads the one response shape LiteLLM gives for every vendor.

Set ``LITELLM_PROXY_API_BASE`` to the proxy's URL and ``LITELLM_PROXY_API_KEY`` to a key for it -- the names
LiteLLM's own SDK reads. The proxy's operator names each search tool; a provider calls the vendor's name (``exa``,
``tavily``, ``parallel``) unless ``search_tool`` names another.

Install with the ``search`` extra.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from web_router.constants_litellm import (
    ENV_LITELLM_PROXY_API_BASE,
    ENV_LITELLM_PROXY_API_KEY,
    LITELLM_API_KEY_HEADER,
    LITELLM_API_KEY_PREFIX,
    LITELLM_DATE_FIELD,
    LITELLM_EXCERPTS_FIELD,
    LITELLM_MAPPED_RESULT_FIELDS,
    LITELLM_MAX_RESULTS_FIELD,
    LITELLM_QUERY_FIELD,
    LITELLM_RESULTS_FIELD,
    LITELLM_SEARCH_PATH,
    LITELLM_SEARCH_TOOL_BY_PROVIDER,
    LITELLM_SNIPPET_FIELD,
    PROVIDER_EXA_LITELLM,
    PROVIDER_PARALLEL_LITELLM,
    PROVIDER_TAVILY_LITELLM,
)
from web_router.constants_tavily import TAVILY_SEARCH_PARAMETERS
from web_router.exceptions import ConfigurationError
from web_router.providers._http import HttpSearchProvider
from web_router.providers._search_text import as_snippet, lite_body
from web_router.providers._utils import best_text
from web_router.providers.exa import ExaProvider
from web_router.providers.parallel_search import ParallelProvider
from web_router.providers.tavily import TavilyProvider, search_fields
from web_router.types import SearchRequest, SearchResponse, SearchResult


class LiteLLMSearchMixin:
    """Mixed in ahead of a vendor's :class:`HttpSearchProvider` to send its search through a LiteLLM proxy."""

    name: ClassVar[str]
    # Never the vendor's host: without a proxy URL the call fails before sending, rather than reach the vendor
    # with the proxy's key.
    base_url: ClassVar[str] = ""
    search_path: ClassVar[str]
    env_api_key: ClassVar[str | None] = ENV_LITELLM_PROXY_API_KEY
    api_key_header: ClassVar[str] = LITELLM_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = LITELLM_API_KEY_PREFIX

    def __init__(self, *, base_url: str | None = None, search_tool: str | None = None, **kwargs: Any) -> None:
        """
        Args:
            base_url: The proxy's URL. Falls back to ``LITELLM_PROXY_API_BASE``.
            search_tool: The ``search_tool_name`` the proxy lists this vendor under. Defaults to the vendor's name.
            **kwargs: Forwarded to :class:`HttpSearchProvider`; ``api_key`` falls back to ``LITELLM_PROXY_API_KEY``.

        Raises:
            The proxy URL is checked on the first call, not here, as ``agentcore`` checks its gateway: a router
            builds every configured backend up front.
        """
        self._proxy_url = (base_url or os.environ.get(ENV_LITELLM_PROXY_API_BASE) or "").strip().rstrip("/")
        super().__init__(base_url=self._proxy_url or None, **kwargs)  # type: ignore[call-arg]
        if search_tool:
            self.search_path = LITELLM_SEARCH_PATH.format(tool=search_tool)  # type: ignore[misc]

    def _vendor_payload(self, request: SearchRequest) -> dict[str, Any]:
        """The vendor's own request body for ``request``."""
        payload: dict[str, Any] = super()._build_payload(request)  # type: ignore[misc]
        return payload

    def _require_proxy(self) -> None:
        if not self._proxy_url:
            raise ConfigurationError.missing_setting(self.name, env_var=ENV_LITELLM_PROXY_API_BASE, setting="base_url")

    def _search(self, request: SearchRequest) -> SearchResponse:
        self._require_proxy()
        response: SearchResponse = super()._search(request)  # type: ignore[misc]
        return response

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        self._require_proxy()
        response: SearchResponse = await super()._asearch(request)  # type: ignore[misc]
        return response

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return {
            **self._vendor_payload(request),
            LITELLM_QUERY_FIELD: request.query,
            **({LITELLM_MAX_RESULTS_FIELD: request.max_results} if request.max_results is not None else {}),
        }

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[_to_result(hit, request) for hit in payload.get(LITELLM_RESULTS_FIELD) or []],
            raw=payload,
        )


def _to_result(hit: dict[str, Any], request: SearchRequest) -> SearchResult:
    # The snippet is the vendor's body for the hit -- Exa's text, Tavily's content, Parallel's first excerpt -- and
    # Parallel's other excerpts ride beside it.
    snippet = hit.get(LITELLM_SNIPPET_FIELD) or ""
    excerpts = [excerpt for excerpt in hit.get(LITELLM_EXCERPTS_FIELD) or [] if excerpt and excerpt != snippet]
    return SearchResult(
        url=hit.get("url", ""),
        title=hit.get("title") or "",
        description=as_snippet(snippet),
        content=lite_body(best_text("\n\n".join([snippet, *excerpts]), snippet), request.search_depth),
        published_date=hit.get(LITELLM_DATE_FIELD),
        extra={k: v for k, v in hit.items() if k not in LITELLM_MAPPED_RESULT_FIELDS},
    )


class ExaLiteLLMProvider(LiteLLMSearchMixin, ExaProvider):
    """Exa search through a LiteLLM proxy, mapped as :class:`ExaProvider` maps it."""

    name: ClassVar[str] = PROVIDER_EXA_LITELLM
    search_path: ClassVar[str] = LITELLM_SEARCH_PATH.format(tool=LITELLM_SEARCH_TOOL_BY_PROVIDER[PROVIDER_EXA_LITELLM])


class ParallelLiteLLMProvider(LiteLLMSearchMixin, ParallelProvider):
    """Parallel search through a LiteLLM proxy, mapped as :class:`ParallelProvider` maps it."""

    name: ClassVar[str] = PROVIDER_PARALLEL_LITELLM
    search_path: ClassVar[str] = LITELLM_SEARCH_PATH.format(
        tool=LITELLM_SEARCH_TOOL_BY_PROVIDER[PROVIDER_PARALLEL_LITELLM]
    )


class TavilyLiteLLMProvider(LiteLLMSearchMixin, HttpSearchProvider):
    """Tavily search through a LiteLLM proxy, mapped as :class:`TavilyProvider` maps it.

    Over plain HTTP rather than Tavily's SDK, so it carries the mapping, not the provider.
    """

    name: ClassVar[str] = PROVIDER_TAVILY_LITELLM
    search_path: ClassVar[str] = LITELLM_SEARCH_PATH.format(
        tool=LITELLM_SEARCH_TOOL_BY_PROVIDER[PROVIDER_TAVILY_LITELLM]
    )
    supported_parameters: ClassVar[frozenset[str]] = TAVILY_SEARCH_PARAMETERS
    refusal_reasons: ClassVar[dict[str, str]] = TavilyProvider.refusal_reasons

    refused_options = TavilyProvider.refused_options

    def _vendor_payload(self, request: SearchRequest) -> dict[str, Any]:
        return search_fields(request)

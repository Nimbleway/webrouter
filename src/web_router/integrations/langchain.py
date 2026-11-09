"""Search, extract and answer as LangChain tools.

    from langchain.agents import create_agent
    from web_router import WebRouter
    from web_router.integrations.langchain import web_router_tools

    router = WebRouter("nimble")
    agent = create_agent("openai:gpt-4.1", tools=web_router_tools(router, max_results=5))

The router's options -- the provider, ``max_results``, ``search_depth``, domain
filters -- are fixed when a tool is built, and the model supplies only the
query or the URLs. That is what keeps a tool the same whichever backend serves
it: a model free to set ``country`` would fail on the providers that do not map
it, and the agent's behaviour would depend on who answered.

Each tool answers in LangChain's ``content_and_artifact`` form: the model reads
a compact JSON rendering, cut to a per-result budget, and the tool message's
``artifact`` carries the untouched :class:`SearchResponse` or
:class:`ExtractResponse` (or :class:`AnswerResponse`), so nothing the backend
returned is lost to the caller.
A backend failure -- a ``ProviderError``, which is also what a URL the model
got wrong comes back as -- reaches the model as a one-line message it can react
to rather than ending the run. Every other error is the caller's setup (a
missing key, an option the provider does not map) and raises, because no
retry by the model would fix it and a message would hide it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import (
    INTEGRATION_DOCUMENT_CHARS,
    INTEGRATION_EXTRACT_TOOL_DESCRIPTION,
    INTEGRATION_RESULT_CHARS,
    INTEGRATION_SEARCH_TOOL_DESCRIPTION,
    LANGCHAIN_EXTRACT_TOOL_NAME,
    LANGCHAIN_SEARCH_TOOL_NAME,
)
from web_router.constants_answer import INTEGRATION_ANSWER_TOOL_DESCRIPTION, LANGCHAIN_ANSWER_TOOL_NAME
from web_router.exceptions import ProviderError
from web_router.integrations._langchain_base import AnswerInput, BaseTool, ExtractInput, SearchInput, _WebRouterTool
from web_router.integrations._options import extract_provider_for, tool_provider
from web_router.integrations._render import render_answer, render_extract, render_search
from web_router.types import (
    ExtractFormat,
    ExtractResponse,
    Provider,
    Recency,
    ResearchEffort,
    SearchDepth,
    SearchResponse,
)
from web_router.types_answer import AnswerResponse


class WebRouterSearch(_WebRouterTool):
    """A web search through web-router, as a LangChain tool.

    Every option but the query is the caller's, set here and sent unchanged on
    every call, so a provider that cannot serve one raises on the first call
    rather than the model learning a different tool per backend.
    """

    name: str = LANGCHAIN_SEARCH_TOOL_NAME
    description: str = INTEGRATION_SEARCH_TOOL_DESCRIPTION
    args_schema: type[BaseModel] = SearchInput

    max_results: int | None = None
    search_depth: SearchDepth | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    country: str | None = None
    locale: str | None = None
    recency: Recency | None = None
    effort: ResearchEffort | None = None
    provider_options: dict[str, Any] | None = None
    result_chars: int = INTEGRATION_RESULT_CHARS
    """Characters of each result's text the model reads; the artifact keeps the whole of it."""

    def _options(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "max_results": self.max_results,
            "search_depth": self.search_depth,
            "include_domains": self.include_domains,
            "exclude_domains": self.exclude_domains,
            "country": self.country,
            "locale": self.locale,
            "recency": self.recency,
            "effort": self.effort,
            "provider_options": self.provider_options,
        }

    def _run(self, query: str, **_: Any) -> tuple[str, SearchResponse]:
        try:
            response = self._sync_router().search(query, **self._options())
        except ProviderError as exc:
            raise self._failed(exc) from exc
        return render_search(response, self.result_chars), response

    async def _arun(self, query: str, **kwargs: Any) -> tuple[str, SearchResponse]:
        if self.async_router is None:
            result: tuple[str, SearchResponse] = await super()._arun(query, **kwargs)
            return result
        try:
            response = await self.async_router.search(query, **self._options())
        except ProviderError as exc:
            raise self._failed(exc) from exc
        return render_search(response, self.result_chars), response


class WebRouterExtract(_WebRouterTool):
    """Page contents through web-router, as a LangChain tool.

    A URL that could not be read is named in what the model sees rather than
    failing the call, as a partly failed extract stays partly successful.
    """

    name: str = LANGCHAIN_EXTRACT_TOOL_NAME
    description: str = INTEGRATION_EXTRACT_TOOL_DESCRIPTION
    args_schema: type[BaseModel] = ExtractInput

    format: ExtractFormat | None = None
    provider_options: dict[str, Any] | None = None
    document_chars: int = INTEGRATION_DOCUMENT_CHARS
    """Characters of each page the model reads; the artifact keeps the whole of it."""

    def _options(self) -> dict[str, Any]:
        return {"provider": self.provider, "format": self.format, "provider_options": self.provider_options}

    def _run(self, urls: list[str], **_: Any) -> tuple[str, ExtractResponse]:
        try:
            response = self._sync_router().extract(urls, **self._options())
        except ProviderError as exc:
            raise self._failed(exc) from exc
        return render_extract(response, self.document_chars), response

    async def _arun(self, urls: list[str], **kwargs: Any) -> tuple[str, ExtractResponse]:
        if self.async_router is None:
            result: tuple[str, ExtractResponse] = await super()._arun(urls, **kwargs)
            return result
        try:
            response = await self.async_router.extract(urls, **self._options())
        except ProviderError as exc:
            raise self._failed(exc) from exc
        return render_extract(response, self.document_chars), response


class WebRouterAnswer(_WebRouterTool):
    """A backend's written answer to one question, with its sources, through web-router, as a LangChain tool.

    Not among :func:`web_router_tools`: every answer backend needs a key and
    bills each call, so it is added by name. Unset, ``provider`` is the
    router preset's answer backend, else the router's default answer backend,
    and with none to use the first call raises
    :class:`~web_router.exceptions.NoAnswerProviderError`.
    """

    name: str = LANGCHAIN_ANSWER_TOOL_NAME
    description: str = INTEGRATION_ANSWER_TOOL_DESCRIPTION
    args_schema: type[BaseModel] = AnswerInput

    provider_options: dict[str, Any] | None = None
    result_chars: int = INTEGRATION_RESULT_CHARS
    """Characters of each citation's snippet the model reads; the artifact keeps the whole of it."""

    def _run(self, query: str, **_: Any) -> tuple[str, AnswerResponse]:
        try:
            response = self._sync_router().answer(query, provider=self.provider, provider_options=self.provider_options)
        except ProviderError as exc:
            raise self._failed(exc) from exc
        return render_answer(response, self.result_chars), response

    async def _arun(self, query: str, **kwargs: Any) -> tuple[str, AnswerResponse]:
        if self.async_router is None:
            result: tuple[str, AnswerResponse] = await super()._arun(query, **kwargs)
            return result
        try:
            response = await self.async_router.answer(
                query, provider=self.provider, provider_options=self.provider_options
            )
        except ProviderError as exc:
            raise self._failed(exc) from exc
        return render_answer(response, self.result_chars), response


def web_router_tools(
    router: WebRouter | None = None,
    *,
    async_router: AsyncWebRouter | None = None,
    provider: str | Provider | None = None,
    extract_provider: str | Provider | None = None,
    **search_options: Any,
) -> list[BaseTool]:
    """Both tools on one router: a search and an extract.

    ``search_options`` are :class:`WebRouterSearch`'s -- ``max_results``,
    ``search_depth`` and the rest. ``extract_provider`` defaults to the router
    preset's extract backend, else to ``provider`` when that one also extracts,
    else to the router's default, since a name that searches is not always one
    that extracts; when that default cannot extract either, it raises
    ``ConfigurationError``.
    Left unset, ``router`` is one default router the two tools share.
    """
    router = router if router is not None else WebRouter()
    search_name = tool_provider(provider)
    extract_name = extract_provider_for(provider, extract_provider, router, async_router)
    return [
        WebRouterSearch(router=router, async_router=async_router, provider=search_name, **search_options),
        WebRouterExtract(router=router, async_router=async_router, provider=extract_name),
    ]


__all__ = [
    "AnswerInput",
    "ExtractInput",
    "SearchInput",
    "WebRouterAnswer",
    "WebRouterExtract",
    "WebRouterSearch",
    "render_answer",
    "render_extract",
    "render_search",
    "web_router_tools",
]

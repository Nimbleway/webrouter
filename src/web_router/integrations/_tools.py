"""The executor behind the Claude and OpenAI tools, and everything about them that is not the vendor's.

Both hand a model the same two tools -- one name, one input schema and one
description each -- and run its calls here, on the caller's keys. What differs
is only the envelope: how a definition is wrapped for the vendor's API, and how
a result goes back. :mod:`web_router.integrations.anthropic` and
:mod:`web_router.integrations.openai` add that and nothing else, so a model
sees the same tools, the same rendering and the same errors from either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import (
    INTEGRATION_DOCUMENT_CHARS,
    INTEGRATION_EXTRACT_MAX_URLS,
    INTEGRATION_EXTRACT_TOOL_DESCRIPTION,
    INTEGRATION_EXTRACT_TOOL_NAME,
    INTEGRATION_EXTRACT_URLS_DESCRIPTION,
    INTEGRATION_METHODS,
    INTEGRATION_MSG_TOOL_ERROR,
    INTEGRATION_RESULT_CHARS,
    INTEGRATION_ROUTING_GUIDE_INTRO,
    INTEGRATION_ROUTING_GUIDE_METHODS,
    INTEGRATION_ROUTING_GUIDE_OUTRO,
    INTEGRATION_SEARCH_QUERY_DESCRIPTION,
    INTEGRATION_SEARCH_TOOL_DESCRIPTION,
    INTEGRATION_SEARCH_TOOL_NAME,
    MSG_INTEGRATION_BAD_INPUT,
    MSG_INTEGRATION_UNKNOWN_METHOD,
    MSG_INTEGRATION_UNKNOWN_TOOL,
)
from web_router.exceptions import ProviderError
from web_router.integrations._options import extract_provider_for, tool_provider
from web_router.integrations._render import render_extract, render_search
from web_router.types import ExtractFormat, ExtractResponse, Provider, SearchDepth, SearchResponse


@dataclass(frozen=True)
class ToolSpec:
    """One tool as every vendor describes it: a name, what it does, and the JSON Schema of its input."""

    name: str
    description: str
    schema: dict[str, Any]


TOOLS: dict[str, ToolSpec] = {
    "search": ToolSpec(
        name=INTEGRATION_SEARCH_TOOL_NAME,
        description=INTEGRATION_SEARCH_TOOL_DESCRIPTION,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": INTEGRATION_SEARCH_QUERY_DESCRIPTION}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "extract": ToolSpec(
        name=INTEGRATION_EXTRACT_TOOL_NAME,
        description=INTEGRATION_EXTRACT_TOOL_DESCRIPTION,
        schema={
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": INTEGRATION_EXTRACT_MAX_URLS,
                    "description": INTEGRATION_EXTRACT_URLS_DESCRIPTION,
                }
            },
            "required": ["urls"],
            "additionalProperties": False,
        },
    ),
}
"""The tools by method, in the order of ``INTEGRATION_METHODS``."""


@dataclass
class ToolResult:
    """One tool call's outcome: what the model reads, and everything it does not."""

    content: str
    """The text for the model -- a compact rendering, or a one-line error."""

    is_error: bool = False
    """True when the backend failed; the model reads why and may try again."""

    response: SearchResponse | ExtractResponse | None = field(default=None, repr=False)
    """The untouched response, for the caller: the model reads only ``content``."""

    @property
    def provider(self) -> str | None:
        """Who served the call -- for ``auto``, the member that answered."""
        return self.response.provider if self.response is not None else None


ResultT = TypeVar("ResultT", bound=ToolResult)


class WebRouterTools(Generic[ResultT]):
    """web-router's search and extract as a model's tools, with the executor that runs them.

    Options are the caller's, fixed here and sent unchanged on every call. A
    backend failure comes back as an error result the model can react to;
    every other error is the caller's setup -- a missing key, an option the
    provider does not map -- and raises, because no retry by the model fixes it.
    """

    _result: type[ResultT]
    """What a call returns: the shared result, with the vendor's way of sending it back."""

    def __init__(
        self,
        router: WebRouter | AsyncWebRouter | None = None,
        *,
        methods: Sequence[str] = INTEGRATION_METHODS,
        provider: str | Provider | None = None,
        extract_provider: str | Provider | None = None,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        format: ExtractFormat | None = None,
        result_chars: int = INTEGRATION_RESULT_CHARS,
        document_chars: int = INTEGRATION_DOCUMENT_CHARS,
    ) -> None:
        """
        Args:
            router: Serves the calls; a sync router for :meth:`call`, an async
                one for :meth:`acall` (a sync router serves both, the async
                call on a worker thread). Unset, a default :class:`WebRouter`.
            methods: Which tools to expose, from ``("search", "extract")``.
            provider: The search backend; unset, the router's default.
            extract_provider: The extract backend. Unset, ``provider`` when it
                also extracts, else the router's default -- a name that
                searches is not always one that extracts.

        Raises:
            ConfigurationError: The extract tool is exposed and neither
                ``provider`` nor the router's default extracts.
        """
        unknown = [m for m in methods if m not in INTEGRATION_METHODS]
        if unknown:
            raise ValueError(MSG_INTEGRATION_UNKNOWN_METHOD.format(known=INTEGRATION_METHODS, method=unknown[0]))
        self.router = router if router is not None else WebRouter()
        self.methods = tuple(m for m in INTEGRATION_METHODS if m in methods)
        self.provider = tool_provider(provider)
        self.extract_provider = (
            extract_provider_for(provider, extract_provider, self.router)
            if "extract" in self.methods
            else tool_provider(extract_provider)
        )
        self.search_options: dict[str, Any] = {
            "provider": self.provider,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_domains": include_domains,
            "exclude_domains": exclude_domains,
            "country": country,
            "locale": locale,
        }
        self.format = format
        self.result_chars = result_chars
        self.document_chars = document_chars

    @property
    def names(self) -> tuple[str, ...]:
        """The tool names the model sees, in the order of :attr:`methods`."""
        return tuple(TOOLS[m].name for m in self.methods)

    def guide(self) -> str:
        """:func:`routing_guide` for exactly the tools this instance exposes."""
        return routing_guide(self.methods)

    def _specs(self) -> list[ToolSpec]:
        return [TOOLS[m] for m in self.methods]

    def _call(self, name: str, tool_input: dict[str, Any]) -> ResultT:
        router = self.router
        if isinstance(router, AsyncWebRouter):
            return asyncio.run(self._acall(name, tool_input))
        method = self._method(name)
        if problem := _invalid(method, name, tool_input):
            return self._result(problem, is_error=True)
        try:
            if method == "search":
                search = router.search(tool_input["query"], **self.search_options)
                return self._result(render_search(search, self.result_chars), response=search)
            extract = router.extract(tool_input["urls"], provider=self.extract_provider, format=self.format)
            return self._result(render_extract(extract, self.document_chars), response=extract)
        except ProviderError as exc:
            return self._result(INTEGRATION_MSG_TOOL_ERROR.format(tool=name, error=exc), is_error=True)

    async def _acall(self, name: str, tool_input: dict[str, Any]) -> ResultT:
        router = self.router
        if isinstance(router, WebRouter):
            return await asyncio.to_thread(self._call, name, tool_input)
        method = self._method(name)
        if problem := _invalid(method, name, tool_input):
            return self._result(problem, is_error=True)
        try:
            if method == "search":
                search = await router.search(tool_input["query"], **self.search_options)
                return self._result(render_search(search, self.result_chars), response=search)
            extract = await router.extract(tool_input["urls"], provider=self.extract_provider, format=self.format)
            return self._result(render_extract(extract, self.document_chars), response=extract)
        except ProviderError as exc:
            return self._result(INTEGRATION_MSG_TOOL_ERROR.format(tool=name, error=exc), is_error=True)

    def _method(self, name: str) -> str:
        for method in self.methods:
            if TOOLS[method].name == name:
                return method
        raise ValueError(MSG_INTEGRATION_UNKNOWN_TOOL.format(name=name, names=", ".join(self.names)))

    def __repr__(self) -> str:
        return f"<{type(self).__name__} methods={self.methods} provider={self.provider!r}>"


def routing_guide(methods: Sequence[str] = INTEGRATION_METHODS) -> str:
    """The system prompt that tells an agent when to reach for which web-router tool.

    One paragraph per method in ``methods``, cheapest first, between a line on
    the principle -- the cheapest tool that answers -- and one on treating web
    content as data. The Claude and OpenAI tools share their names, so the
    guide reads the same for both.
    """
    names = {method: spec.name for method, spec in TOOLS.items()}
    paragraphs = [INTEGRATION_ROUTING_GUIDE_METHODS[m].format(**names) for m in INTEGRATION_METHODS if m in methods]
    return "\n\n".join([INTEGRATION_ROUTING_GUIDE_INTRO, *paragraphs, INTEGRATION_ROUTING_GUIDE_OUTRO])


def _invalid(method: str, name: str, tool_input: dict[str, Any]) -> str | None:
    """What the model reads when its input breaks the tool's schema; ``None`` when it fits."""
    if method == "search":
        query = tool_input.get("query")
        ok = isinstance(query, str) and bool(query.strip())
    else:
        urls = tool_input.get("urls")
        ok = (
            isinstance(urls, list)
            and 1 <= len(urls) <= INTEGRATION_EXTRACT_MAX_URLS
            and all(isinstance(url, str) and url for url in urls)
        )
    return None if ok else MSG_INTEGRATION_BAD_INPUT[method].format(tool=name, max_urls=INTEGRATION_EXTRACT_MAX_URLS)

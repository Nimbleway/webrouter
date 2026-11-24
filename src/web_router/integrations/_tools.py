"""The executor behind the Claude and OpenAI tools, and everything about them that is not the vendor's.

Both hand a model the same tools -- one name, one input schema and one
description each -- and run its calls here, on the caller's keys. What differs
is only the envelope: how a definition is wrapped for the vendor's API, and how
a result goes back. :mod:`web_router.integrations.anthropic` and
:mod:`web_router.integrations.openai` add that and nothing else, so a model
sees the same tools, the same rendering and the same errors from either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Generic, TypeVar

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import (
    INTEGRATION_DOCUMENT_CHARS,
    INTEGRATION_METHODS,
    INTEGRATION_RESULT_CHARS,
)
from web_router.constants_invoke import MSG_TOOLS_SYNC_IN_LOOP
from web_router.exceptions import ConfigurationError
from web_router.integrations._guide import routing_guide
from web_router.integrations._messages import INTEGRATION_TIMEOUT, INTEGRATION_URL_POLICY
from web_router.integrations._options import backends, call_options, exposed, tool_provider
from web_router.integrations._plan import failed, plan, rendered
from web_router.integrations._result import ToolResult
from web_router.integrations._specs import TOOLS, ToolSpec
from web_router.integrations.errors import TOOL_FAILURES
from web_router.routing._runner import _on_private_loop
from web_router.routing.url_policy import UrlPolicy, url_check
from web_router.types import ExtractFormat, Provider, Recency, ResearchEffort, SearchDepth

ResultT = TypeVar("ResultT", bound=ToolResult)


class WebRouterTools(Generic[ResultT]):
    """web-router's search, extract and answer as a model's tools, with the executor that runs them.

    Options are the caller's, fixed here and sent unchanged on every call. A
    backend failure, a call out of time and input the model got wrong come
    back as a short error result the model can react to; every other error is
    the caller's setup -- a missing key, an option the provider does not map --
    and raises, because no retry by the model fixes it.

    Args:
        router: Serves the calls; a sync router for :meth:`call`, an async
            one for :meth:`acall` (a sync router serves both, the async
            call on a worker thread). Unset, a default :class:`WebRouter`.
        methods: Which tools to expose, from ``("search", "extract",
            "answer")``; unset, search and extract. ``answer`` bills each
            call on a backend that needs a key, so it is asked for by name.
        provider: The search backend; unset, the router's default.
        extract_provider: The extract backend. Unset, ``provider`` when it
            also extracts, else the router's default -- a name that
            searches is not always one that extracts.
        answer_provider: The answer backend. Unset, ``provider`` when it
            also answers, else the router's default answer backend. The
            answer tool sends only the question: the search options above
            are not passed to it.
        timeout: Seconds a call may take before the model reads a ``timeout`` error; ``None`` lifts it.
        url_policy: ``"public_only"`` refuses (never sends) a model's URL whose host is private,
            loopback, link-local or a metadata address; ``None`` sends any http(s) URL.

    Raises:
        ConfigurationError: The extract tool is exposed and neither
            ``provider`` nor the router's default extracts, or
            ``url_policy`` is not one the router knows.
        NoAnswerProviderError: The answer tool is exposed, no backend is
            named, and the router has no single answer backend to use.
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
        answer_provider: str | Provider | None = None,
        max_results: int | None = None,
        search_depth: SearchDepth | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str | None = None,
        locale: str | None = None,
        recency: Recency | None = None,
        effort: ResearchEffort | None = None,
        format: ExtractFormat | None = None,
        result_chars: int = INTEGRATION_RESULT_CHARS,
        document_chars: int = INTEGRATION_DOCUMENT_CHARS,
        timeout: float | None = INTEGRATION_TIMEOUT,
        url_policy: UrlPolicy | None = INTEGRATION_URL_POLICY,
    ) -> None:
        """Fix the tools' options and resolve their backends once; the class docstring says what each one does."""
        self.methods = exposed(methods)
        self.router = router if router is not None else WebRouter()
        self.provider = tool_provider(provider)
        self.extract_provider, self.answer_provider = backends(
            self.methods, provider, extract_provider, answer_provider, self.router
        )
        self.search_options, self._options = call_options(
            {
                "provider": self.provider,
                "max_results": max_results,
                "search_depth": search_depth,
                "include_domains": include_domains,
                "exclude_domains": exclude_domains,
                "country": country,
                "locale": locale,
                "recency": recency,
                "effort": effort,
            },
            answer={"provider": self.answer_provider},
            extract={"provider": self.extract_provider, "format": format},
            timeout=timeout,
        )
        self._url_check = url_check(url_policy)
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

    def _call(self, name: str, tool_input: object) -> ResultT:
        router = self.router
        if isinstance(router, AsyncWebRouter):
            if _loop_running():  # blocking here would stall that loop for the whole call
                raise ConfigurationError(MSG_TOOLS_SYNC_IN_LOOP)
            result: ResultT = _on_private_loop(self._acall(name, tool_input)).result()  # one loop for every call
            return result
        planned = plan(name, tool_input, self.methods, self._url_check)
        if isinstance(planned, str):
            return self._result(planned, is_error=True)
        try:
            response = getattr(router, planned.method)(planned.target, **self._options[planned.method])
        except TOOL_FAILURES as exc:
            return self._result(failed(planned, exc, self.result_chars), is_error=True, error=exc)
        return self._result(rendered(planned, response, self.result_chars, self.document_chars), response=response)

    async def _acall(self, name: str, tool_input: object) -> ResultT:
        router = self.router
        if isinstance(router, WebRouter):
            return await asyncio.to_thread(self._call, name, tool_input)
        # Planning screens URLs synchronously, so keep its DNS lookup off the loop.
        planned = await asyncio.to_thread(plan, name, tool_input, self.methods, self._url_check)
        if isinstance(planned, str):
            return self._result(planned, is_error=True)
        try:
            response = await getattr(router, planned.method)(planned.target, **self._options[planned.method])
        except TOOL_FAILURES as exc:
            return self._result(failed(planned, exc, self.result_chars), is_error=True, error=exc)
        return self._result(rendered(planned, response, self.result_chars, self.document_chars), response=response)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} methods={self.methods} provider={self.provider!r}>"


def _loop_running() -> bool:
    """Whether an event loop is running in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True

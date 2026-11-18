"""What the LangChain tools share: the input models the model fills in, and the router and error handling.

Kept apart from :mod:`web_router.integrations.langchain`, which re-exports the
input models; importing either needs langchain-core.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import (
    EXTRA_LANGCHAIN,
    INTEGRATION_EXTRACT_MAX_URLS,
    INTEGRATION_EXTRACT_URLS_DESCRIPTION,
    INTEGRATION_RESULT_CHARS,
    INTEGRATION_SEARCH_QUERY_DESCRIPTION,
    MSG_LANGCHAIN_NOT_INSTALLED,
)
from web_router.constants_answer import INTEGRATION_ANSWER_QUERY_DESCRIPTION
from web_router.integrations._messages import INTEGRATION_TIMEOUT
from web_router.integrations._plan import Planned, failed, planned_extract
from web_router.integrations.errors import TOOL_FAILURES
from web_router.routing.url_policy import UrlPolicy, url_check

try:
    from langchain_core.tools import BaseTool, ToolException
except ImportError as exc:  # pragma: no cover - exercised by the import-without-extras CI job
    raise ImportError(MSG_LANGCHAIN_NOT_INSTALLED.format(extra=EXTRA_LANGCHAIN)) from exc

T = TypeVar("T")


class SearchInput(BaseModel):
    """What the model supplies to :class:`WebRouterSearch`."""

    query: str = Field(min_length=1, description=INTEGRATION_SEARCH_QUERY_DESCRIPTION)


class ExtractInput(BaseModel):
    """What the model supplies to :class:`WebRouterExtract`."""

    urls: list[str] = Field(
        min_length=1, max_length=INTEGRATION_EXTRACT_MAX_URLS, description=INTEGRATION_EXTRACT_URLS_DESCRIPTION
    )


class AnswerInput(BaseModel):
    """What the model supplies to :class:`WebRouterAnswer`."""

    query: str = Field(min_length=1, description=INTEGRATION_ANSWER_QUERY_DESCRIPTION)


class _WebRouterTool(BaseTool):
    """The router, the provider and the error handling every tool shares."""

    router: WebRouter | None = Field(default=None, exclude=True)
    """Serves the sync calls. Left unset, the tool builds a default one on first use."""

    async_router: AsyncWebRouter | None = Field(default=None, exclude=True)
    """Serves the async calls when given; without one they run the sync router on a worker thread."""

    provider: str | None = None
    """The backend every call goes to; unset, the router's default -- ``auto`` unless it was built with one."""

    timeout: float | None = INTEGRATION_TIMEOUT
    """Seconds each call may take; past it, the model reads a ``timeout`` failure. ``None`` lifts it."""

    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    handle_tool_error: bool = True

    def _sync_router(self) -> WebRouter:
        if self.router is None:
            self.router = WebRouter()
        return self.router

    def _planned(self, urls: list[str], url_policy: UrlPolicy | None) -> Planned:
        """An extract of the URLs ``url_policy`` admits; when it refuses every one, what the model reads is raised."""
        planned = planned_extract(self.name, urls, url_check(url_policy))
        if isinstance(planned, str):
            raise ToolException(planned)
        return planned

    def _guarded(self, call: Callable[[], T], planned: Planned | None = None) -> T:
        """``call()``, with a backend failure or a call out of time raised as the short message the model reads."""
        try:
            return call()
        except TOOL_FAILURES as exc:
            raise self._failed(exc, planned) from exc

    async def _aguarded(self, call: Awaitable[T], planned: Planned | None = None) -> T:
        """:meth:`_guarded` for an awaited call."""
        try:
            return await call
        except TOOL_FAILURES as exc:
            raise self._failed(exc, planned) from exc

    def _failed(self, exc: BaseException, planned: Planned | None) -> ToolException:
        """What the model reads: the Claude and OpenAI tools' text, from the error's fields, never the vendor's."""
        return ToolException(failed(planned or Planned(self.name, "search"), exc, INTEGRATION_RESULT_CHARS))


__all__ = ["AnswerInput", "BaseTool", "ExtractInput", "SearchInput", "_WebRouterTool"]

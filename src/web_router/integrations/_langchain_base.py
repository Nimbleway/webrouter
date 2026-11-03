"""What the LangChain tools share: the input models the model fills in, and the router and error handling.

Kept apart from :mod:`web_router.integrations.langchain`, which re-exports the
input models; importing either needs langchain-core.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from web_router.client import AsyncWebRouter, WebRouter
from web_router.constants import (
    EXTRA_LANGCHAIN,
    INTEGRATION_EXTRACT_MAX_URLS,
    INTEGRATION_EXTRACT_URLS_DESCRIPTION,
    INTEGRATION_MSG_TOOL_ERROR,
    INTEGRATION_SEARCH_QUERY_DESCRIPTION,
    MSG_LANGCHAIN_NOT_INSTALLED,
)
from web_router.constants_answer import INTEGRATION_ANSWER_QUERY_DESCRIPTION
from web_router.exceptions import ProviderError

try:
    from langchain_core.tools import BaseTool, ToolException
except ImportError as exc:  # pragma: no cover - exercised by the import-without-extras CI job
    raise ImportError(MSG_LANGCHAIN_NOT_INSTALLED.format(extra=EXTRA_LANGCHAIN)) from exc


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

    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    handle_tool_error: bool = True

    def _sync_router(self) -> WebRouter:
        if self.router is None:
            self.router = WebRouter()
        return self.router

    def _failed(self, exc: ProviderError) -> ToolException:
        return ToolException(INTEGRATION_MSG_TOOL_ERROR.format(tool=self.name, error=exc))


__all__ = ["AnswerInput", "BaseTool", "ExtractInput", "SearchInput", "_WebRouterTool"]

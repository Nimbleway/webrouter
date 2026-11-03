"""The LangChain answer tool: what the model reads, and what the caller keeps."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import ToolMessage

from web_router import AsyncWebRouter, WebRouter
from web_router.integrations.anthropic import WebRouterTools
from web_router.integrations.langchain import WebRouterAnswer, web_router_tools
from web_router.types_answer import AnswerResponse


def test_the_langchain_answer_tool_returns_the_answer_and_keeps_the_response_as_its_artifact(
    fake_answer_provider: type,
) -> None:
    router = WebRouter()
    message = WebRouterAnswer(router=router, provider="fake").invoke(
        {"type": "tool_call", "id": "c", "name": "web_answer", "args": {"query": "who won"}}
    )

    assert isinstance(message, ToolMessage)
    assert isinstance(message.artifact, AnswerResponse)
    assert json.loads(message.content)["answer"] == "the answer"
    claude = WebRouterTools(router, methods=["answer"], provider="fake").call("web_router_answer", {"query": "who won"})
    assert claude.content == message.content


def test_the_langchain_answer_tool_is_not_among_the_default_tools() -> None:
    assert [tool.name for tool in web_router_tools(WebRouter(), provider="fake")] == ["web_search", "web_extract"]


async def test_the_langchain_answer_tool_serves_ainvoke_on_the_async_router(fake_answer_provider: type) -> None:
    async with AsyncWebRouter() as router:
        message = await WebRouterAnswer(async_router=router, provider="fake").ainvoke(
            {"type": "tool_call", "id": "c", "name": "web_answer", "args": {"query": "q"}}
        )

    assert json.loads(message.content)["answer"] == "the answer"

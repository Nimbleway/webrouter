"""web-router's tools for a Claude agent, and the guide that says when to use which.

    from web_router import AsyncWebRouter
    from web_router.integrations.anthropic import WebRouterTools, routing_guide

    tools = WebRouterTools(AsyncWebRouter(), provider="nimble", max_results=5)
    agent = client.beta.agents.create(
        name="researcher",
        model="claude-opus-5",
        system=routing_guide(),
        tools=tools.definitions(),
    )
    # ...then, for each agent.custom_tool_use event in the session:
    result = await tools.acall(event.name, event.input)
    # send {"type": "user.custom_tool_result", "custom_tool_use_id": event.id, **result.event_fields()}

The definitions are plain custom tools, so they serve a Managed Agents agent
and the Messages API's ``tools`` alike, and the module needs no SDK: the
caller's own Anthropic client sends them, and :meth:`WebRouterTools.call` runs
each call locally, on the caller's keys. Every option but the query or the
URLs is fixed here, as it is for the LangChain tools, so the agent behaves the
same whichever backend serves it.

:func:`routing_guide` is the judgment half: a system prompt saying which tool
fits which need, and when a cheaper one is enough. It is a first version, and
the agent benchmark in ``benchmarks/agent/`` is how a change to it is measured.
"""

from __future__ import annotations

from typing import Any

from web_router.constants import ANTHROPIC_TOOL_TYPE
from web_router.integrations import _tools
from web_router.integrations._tools import routing_guide


class ToolResult(_tools.ToolResult):
    """One tool call's outcome: what the model reads, and everything it does not."""

    def event_fields(self) -> dict[str, Any]:
        """The fields a Managed Agents ``user.custom_tool_result`` event carries besides the tool use's id."""
        return {"content": [{"type": "text", "text": self.content}], "is_error": self.is_error}


class WebRouterTools(_tools.WebRouterTools[ToolResult]):
    """web-router's search and extract as Claude custom tools, with the executor that runs them.

    Options are the caller's, fixed here and sent unchanged on every call. A
    backend failure comes back as an error result the model can react to;
    every other error is the caller's setup -- a missing key, an option the
    provider does not map -- and raises, because no retry by the model fixes it.
    """

    _result = ToolResult

    def definitions(self) -> list[dict[str, Any]]:
        """The custom tool definitions, for an agent's ``tools`` or a Messages API request."""
        return [
            {
                "type": ANTHROPIC_TOOL_TYPE,
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.schema,
            }
            for spec in self._specs()
        ]

    def call(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        """Run one tool call the model made, on the sync router."""
        return self._call(name, tool_input)

    async def acall(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        """Run one tool call the model made, on the async router -- or the sync one on a worker thread."""
        return await self._acall(name, tool_input)


__all__ = ["ToolResult", "WebRouterTools", "routing_guide"]

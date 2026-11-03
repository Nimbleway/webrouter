"""web-router's tools for an OpenAI model, and the guide that says when to use which.

    from openai import OpenAI
    from web_router import WebRouter
    from web_router.integrations.openai import WebRouterTools

    client = OpenAI()
    tools = WebRouterTools(WebRouter(), provider="nimble", max_results=5)
    request = {"model": "gpt-4.1", "instructions": tools.guide(), "tools": tools.definitions()}
    response = client.responses.create(input=question, **request)
    while calls := [item for item in response.output if item.type == "function_call"]:
        outputs = [tools.call(c.name, c.arguments).function_call_output(c.call_id) for c in calls]
        response = client.responses.create(input=outputs, previous_response_id=response.id, **request)
    print(response.output_text)

:meth:`WebRouterTools.definitions` are Responses API function tools and
:meth:`~WebRouterTools.chat_definitions` the same tools in Chat Completions'
shape, answered with :meth:`ToolResult.function_call_output` and
:meth:`ToolResult.tool_message`. The module needs no SDK: the caller's own
OpenAI client sends the definitions, and :meth:`WebRouterTools.call` runs each
call locally, on the caller's keys. The tools, their options rule, the
rendering the model reads and :func:`routing_guide` are the Claude tools',
unchanged -- only the envelope is OpenAI's.

The definitions ask for strict mode, so OpenAI decodes the model's arguments
against the schema. That does not replace the executor's own check: asked for
an empty list of URLs, a strict model sent ``[""]``, which a call refuses as
input the model got wrong. A tool's output to OpenAI carries no error flag, so
every error the model reads says in words that the call did not run or failed.
"""

from __future__ import annotations

import json
from typing import Any

from web_router.constants import (
    OPENAI_OUTPUT_ITEM_TYPE,
    OPENAI_STRICT_TOOLS,
    OPENAI_TOOL_MESSAGE_ROLE,
    OPENAI_TOOL_TYPE,
)
from web_router.integrations import _tools
from web_router.integrations._tools import routing_guide


class ToolResult(_tools.ToolResult):
    """One tool call's outcome: what the model reads, and everything it does not.

    ``is_error`` is for the caller; the model has only ``content``, which
    says so itself.
    """

    def function_call_output(self, call_id: str) -> dict[str, Any]:
        """The Responses API input item that answers the ``function_call`` carrying ``call_id``."""
        return {"type": OPENAI_OUTPUT_ITEM_TYPE, "call_id": call_id, "output": self.content}

    def tool_message(self, tool_call_id: str) -> dict[str, Any]:
        """The Chat Completions message that answers the tool call whose ``id`` is ``tool_call_id``."""
        return {"role": OPENAI_TOOL_MESSAGE_ROLE, "tool_call_id": tool_call_id, "content": self.content}


class WebRouterTools(_tools.WebRouterTools[ToolResult]):
    """web-router's search, extract and answer as OpenAI function tools, with the executor that runs them.

    Options are the caller's, fixed here and sent unchanged on every call. A
    backend failure, and arguments the model got wrong, come back as an error
    result the model can react to; every other error is the caller's setup --
    a missing key, an option the provider does not map -- and raises, because
    no retry by the model fixes it.
    """

    _result = ToolResult

    def definitions(self) -> list[dict[str, Any]]:
        """The function tool definitions, for a Responses API request's ``tools``."""
        return [
            {
                "type": OPENAI_TOOL_TYPE,
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.schema,
                "strict": OPENAI_STRICT_TOOLS,
            }
            for spec in self._specs()
        ]

    def chat_definitions(self) -> list[dict[str, Any]]:
        """The same tools in the shape a Chat Completions request's ``tools`` takes."""
        return [
            {
                "type": OPENAI_TOOL_TYPE,
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.schema,
                    "strict": OPENAI_STRICT_TOOLS,
                },
            }
            for spec in self._specs()
        ]

    def call(self, name: str, arguments: str | dict[str, Any]) -> ToolResult:
        """Run one tool call the model made, on the sync router.

        ``arguments`` is the call's JSON string as OpenAI hands it over, or
        that string already decoded.
        """
        return self._call(name, _decoded(arguments))

    async def acall(self, name: str, arguments: str | dict[str, Any]) -> ToolResult:
        """Run one tool call the model made, on the async router -- or the sync one on a worker thread."""
        return await self._acall(name, _decoded(arguments))


def _decoded(arguments: str | dict[str, Any]) -> dict[str, Any]:
    # Arguments that are not a JSON object break the schema as a missing field
    # does, so they get the same answer: an error result naming what the tool needs.
    if isinstance(arguments, dict):
        return arguments
    try:
        decoded = json.loads(arguments)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = ["ToolResult", "WebRouterTools", "routing_guide"]

"""The transport the keyless MCP search providers share: one JSON-RPC ``tools/call`` per search.

You.com's, Exa's and Parallel's hosted MCP servers each answer a lone
``tools/call`` without the ``initialize`` handshake -- checked live -- so a
search stays one request, as it is for every REST backend. What differs between
them is the tool, its arguments, whether the reply is plain JSON or a stream of
server-sent events, and what the tool's text content holds; the first two are a
provider's ``mcp_tool`` and ``_build_payload``, the last its ``_unwrap``.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from web_router.constants import (
    HTTP_ERROR_BODY_LIMIT,
    MCP_ACCEPT,
    MCP_JSONRPC_VERSION,
    MCP_SSE_DATA_PREFIX,
    MSG_HTTP_ERROR,
    MSG_MCP_ERROR,
    MSG_MCP_NO_RESULT,
)
from web_router.exceptions import ProviderError
from web_router.types import SearchRequest


class McpSearchMixin:
    """Mixed in ahead of an :class:`~web_router.providers._http.HttpSearchProvider` to carry its search over MCP.

    The provider's ``_build_payload`` stays the single description of the tool's
    arguments; this wraps it in the JSON-RPC envelope and unwraps the reply.
    """

    name: ClassVar[str]
    mcp_tool: ClassVar[str]
    env_api_key: ClassVar[str | None] = None

    def __init__(self, **kwargs: Any) -> None:
        headers = {"Accept": MCP_ACCEPT, **(kwargs.pop("headers", None) or {})}
        super().__init__(headers=headers, **kwargs)  # type: ignore[call-arg]

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        raise NotImplementedError

    def _call_kwargs(self, request: SearchRequest) -> dict[str, Any]:
        return {
            "json": {
                "jsonrpc": MCP_JSONRPC_VERSION,
                "id": 1,
                "method": "tools/call",
                "params": {"name": self.mcp_tool, "arguments": self._build_payload(request)},
            }
        }

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        """The tool's answer, out of the JSON-RPC reply, whether it came as plain JSON or as an event stream."""
        result = self._rpc_result(response)
        if result.get("isError"):
            raise ProviderError(self.name, MSG_MCP_ERROR.format(error=result))
        text = "".join(block.get("text", "") for block in result.get("content") or [])
        # A tool may answer in structuredContent alone; its _unwrap reads that.
        if not text and result.get("structuredContent") is None:
            raise ProviderError(self.name, MSG_MCP_NO_RESULT)
        return self._unwrap(text, result)

    def _rpc_result(self, response: httpx.Response) -> dict[str, Any]:
        """The ``result`` of a JSON-RPC reply, for any method: an HTTP or JSON-RPC error raises."""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = MSG_HTTP_ERROR.format(
                status=exc.response.status_code, body=exc.response.text[:HTTP_ERROR_BODY_LIMIT]
            )
            raise ProviderError(self.name, detail, original=exc) from exc

        for message in self._messages(response.text):
            if error := message.get("error"):
                raise ProviderError(self.name, MSG_MCP_ERROR.format(error=error))
            # Progress notifications share the stream with the answer; only the
            # reply to our call carries "result".
            if "result" in message:
                result: dict[str, Any] = message["result"]
                return result

        raise ProviderError(self.name, MSG_MCP_NO_RESULT)

    def _unwrap(self, text: str, result: dict[str, Any]) -> dict[str, Any]:
        """The search payload in the tool's text content. JSON unless a provider says otherwise."""
        payload: dict[str, Any] = json.loads(text)
        return payload

    @staticmethod
    def _messages(body: str) -> list[dict[str, Any]]:
        """The JSON-RPC messages in a reply: one plain JSON object, or one per ``data:`` line of a stream."""
        stripped = body.lstrip()
        if stripped.startswith("{"):
            try:
                return [json.loads(stripped)]
            except json.JSONDecodeError:
                return []
        messages = []
        for line in body.splitlines():
            if line.startswith(MCP_SSE_DATA_PREFIX):
                try:
                    messages.append(json.loads(line[len(MCP_SSE_DATA_PREFIX) :]))
                except json.JSONDecodeError:
                    continue
        return messages

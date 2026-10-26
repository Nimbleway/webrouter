"""What the AgentCore tests share: a fake gateway, its replies and fake AWS credentials."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from web_router.providers.agentcore import AgentCoreProvider

GATEWAY = "https://webrouter-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
TOOL = "web-search___WebSearch"

HITS = [
    {
        "text": "Python 3.13 was released on October 7, 2024, featuring an experimental free-threaded mode.",
        "publishedDate": "2024-10-07",
        "url": "https://www.python.org/downloads/release/python-3130/",
        "title": "Python 3.13 Release Highlights",
    },
    {"text": "Python is a programming language created by Guido van Rossum."},
]

FILTERS_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}, "maxResults": {"type": "integer"}, "filters": {"type": "object"}},
    "required": ["query"],
}
NO_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}, "maxResults": {"type": "integer"}},
    "required": ["query"],
}


@pytest.fixture(autouse=True)
def aws_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Fake static credentials, and nothing from the developer's own AWS setup."""
    for var in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
    monkeypatch.delenv("AGENTCORE_GATEWAY_URL", raising=False)


def reply(result: dict[str, Any], *, sse: bool = False) -> httpx.Response:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
    if sse:
        return httpx.Response(
            200, text=f"event: message\ndata: {body}\n\n", headers={"content-type": "text/event-stream"}
        )
    return httpx.Response(200, text=body, headers={"content-type": "application/json"})


def tools(*names: str, schema: dict[str, Any] = FILTERS_SCHEMA) -> dict[str, Any]:
    return {"tools": [{"name": name, "inputSchema": schema} for name in names]}


def gateway(
    call: Callable[[dict[str, Any]], httpx.Response] | None = None,
    *,
    listing: dict[str, Any] | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        body = json.loads(request.content)
        if body["method"] == "tools/list":
            return reply(listing or tools(TOOL))
        if call is not None:
            return call(body)
        return reply({"content": [{"type": "text", "text": json.dumps({"id": "824f89d0", "results": HITS})}]})

    return httpx.MockTransport(handle)


def provider(transport: httpx.MockTransport, url: str = GATEWAY) -> AgentCoreProvider:
    return AgentCoreProvider(gateway_url=url, transport=transport)

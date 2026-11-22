"""Opening an MCP session against the HTTP app in process, with its lifespan running."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from web_router.constants_mcp_http import MCP_HTTP_PATH

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}

ACCEPT = {"Accept": "application/json, text/event-stream"}


async def initialize(app: Any, *requests: Mapping[str, str]) -> list[httpx.Response]:
    """POST ``initialize`` to ``app`` once per header set in ``requests``, with the served app's lifespan running.

    ``app`` is what :func:`~web_router.mcp.http_transport.build_app` returned: the served Starlette app, or
    the token check wrapped around it.
    """
    served = getattr(app, "app", app)
    responses = []
    async with served.router.lifespan_context(served):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            for headers in requests:
                responses.append(await client.post(MCP_HTTP_PATH, headers={**ACCEPT, **headers}, json=INITIALIZE))
    return responses

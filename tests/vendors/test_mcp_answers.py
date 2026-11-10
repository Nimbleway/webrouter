"""The vendor tools' answers over MCP: what a handle keeps, what counts as usable, and the write gate."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router import AsyncWebRouter
from web_router.constants_vendors import MCP_TOOL_CALL_VENDOR
from web_router.mcp.tools import WebRouterTools
from web_router.vendors.caller import VendorCaller


async def _tools(recorder: Recorder, credentials: dict[str, Any]) -> WebRouterTools:
    router = AsyncWebRouter(vendor_credentials=credentials)
    transport = httpx.MockTransport(recorder)
    router._vendor_caller = VendorCaller(router.vendor_credential_source(), async_transport=transport)
    return WebRouterTools(router)


_DATADOG = {"datadog": {"api_key": "k", "app_key": "a"}}
_ECHOED = {"session_id": "sess-abc123", "api_token": "tok-xyz", "live_view_url": "wss://x.example/devtools?key=K"}


@pytest.mark.parametrize("body", [_ECHOED, {"monitors": [1]}])
async def test_the_raw_handle_holds_what_the_answer_shows(body: dict[str, Any]) -> None:
    tools = await _tools(Recorder(lambda request: httpx.Response(200, json=body)), _DATADOG)
    try:
        shown = await tools.call(MCP_TOOL_CALL_VENDOR, {"endpoint": "datadog.monitors.list", "params": {}})
        raw = (await tools.call("fetch_raw", {"handle": shown["handle"]}))["raw"]["data"]
    finally:
        await tools.aclose()
    assert raw == shown["data"]
    if body is _ECHOED:
        assert "sess-abc123" not in str(raw) and "tok-xyz" not in str(raw)

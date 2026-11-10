"""The vendor tools' answers over MCP: what a handle keeps, what counts as usable, and the write gate."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router import AsyncWebRouter
from web_router.constants_vendors import MCP_TOOL_CALL_VENDOR, MCP_TOOL_VENDOR_ENDPOINT, MCP_TOOL_VENDORS
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


@pytest.mark.parametrize(("credentials", "missing"), [({"api_key": "k"}, []), ({}, ["DD_API_KEY"])])
async def test_an_endpoint_is_usable_with_only_the_keys_it_lists(credentials: dict[str, str], missing: list[str]):
    tools = await _tools(Recorder(), {"datadog": credentials})
    try:
        described = await tools.call(MCP_TOOL_VENDOR_ENDPOINT, {"endpoint": "datadog.auth.validate"})
        found = await tools.call(MCP_TOOL_VENDORS, {"query": "validate api key", "vendor": "datadog"})
    finally:
        await tools.aclose()
    assert described["set_to_enable"] == missing
    row = next(row for row in found["endpoints"] if row["endpoint"] == "datadog.auth.validate")
    assert row["configured"] is (not missing)


async def test_an_endpoint_without_its_own_list_still_needs_every_key() -> None:
    tools = await _tools(Recorder(), {"datadog": {"api_key": "k"}})
    try:
        described = await tools.call(MCP_TOOL_VENDOR_ENDPOINT, {"endpoint": "datadog.monitors.list"})
    finally:
        await tools.aclose()
    assert described["set_to_enable"] == ["DD_APPLICATION_KEY"]


async def test_an_endpoint_lists_the_values_a_setting_accepts() -> None:
    tools = await _tools(Recorder(), _DATADOG)
    try:
        described = await tools.call(MCP_TOOL_VENDOR_ENDPOINT, {"endpoint": "datadog.monitors.list"})
    finally:
        await tools.aclose()
    assert "datadoghq.eu" in described["settings"]["site"]["choices"]


@pytest.mark.parametrize(
    "arguments", [{"query": ["google"]}, {"query": "google ads", "limit": "2"}, {"configured": "yes"}]
)
async def test_a_wrong_argument_type_is_a_readable_tool_error(arguments: dict[str, Any]) -> None:
    tools = await _tools(Recorder(), _DATADOG)
    try:
        with pytest.raises(ValueError, match="takes"):
            await tools.call(MCP_TOOL_VENDORS, arguments)
        assert (await tools.call(MCP_TOOL_VENDORS, {"query": "google ads", "limit": 2}))["endpoints"]
    finally:
        await tools.aclose()


def _answer(data: Any) -> Any:
    from web_router.vendors.types import VendorResponse

    return VendorResponse(vendor="datadog", method="GET", url="https://api.datadoghq.com/x", status_code=200, data=data)


@pytest.mark.parametrize(
    ("data", "budget"),
    [
        ({"rows": [{"id": i, "name": f"row {i}"} for i in range(2000)]}, 8000),
        ({"rows": ['say "hi"\nand "bye"\n' * 5 for _ in range(500)]}, 8000),
        ({"rows": list(range(500))}, 300),
    ],
)
def test_a_cut_answer_fits_its_budget(data: Any, budget: int) -> None:
    from web_router.mcp.shaping import render
    from web_router.mcp.shaping_vendors import shape_vendor_response

    shaped = shape_vendor_response(_answer(data), "h1", budget=budget)
    assert shaped["truncated"] is True
    assert len(render(shaped)) <= budget


def test_a_small_answer_comes_back_whole() -> None:
    from web_router.mcp.shaping_vendors import shape_vendor_response

    shaped = shape_vendor_response(_answer({"a": 1}), "h1", budget=8000)
    assert shaped["truncated"] is False
    assert shaped["data"] == {"a": 1}


async def test_vendor_keys_passed_in_code_survive_loading_a_dotenv(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.delenv("TAVILY_API_KEY")  # registered, so the key load_env sets is taken away afterwards
    (tmp_path / ".env").write_text("TAVILY_API_KEY=tvly-test\n", encoding="utf-8")
    tools = WebRouterTools(AsyncWebRouter(vendor_credentials=_DATADOG))
    try:
        await tools.call("load_env", {"path": str(tmp_path / ".env")})
        rows = (await tools.call(MCP_TOOL_VENDORS, {"category": "observability"}))["vendors"]
    finally:
        await tools.aclose()
    assert {row["vendor"]: row for row in rows}["datadog"]["configured"] is True

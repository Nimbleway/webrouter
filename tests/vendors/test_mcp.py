"""The three vendor tools on the MCP server."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router import AsyncWebRouter
from web_router.constants_vendors import (
    MCP_TOOL_CALL_VENDOR,
    MCP_TOOL_VENDOR_ENDPOINT,
    MCP_TOOL_VENDORS,
    VENDOR_ENV_ALLOW_WRITES,
)
from web_router.exceptions_vendors import VendorWriteRefusedError
from web_router.mcp.env import credential_variables
from web_router.mcp.shaping_vendors import shape_vendor_response
from web_router.mcp.tools import WebRouterTools
from web_router.vendors.caller import VendorCaller
from web_router.vendors.types import VendorResponse

DATADOG = {"datadog": {"api_key": "dd-api", "app_key": "dd-app"}}


@pytest.fixture
def recorder() -> Recorder:
    return Recorder(lambda request: httpx.Response(200, json={"monitors": [{"id": 1, "name": "cpu"}]}))


@pytest.fixture
async def tools(recorder: Recorder) -> Any:
    router = AsyncWebRouter(vendor_credentials=DATADOG)
    router._vendor_caller = VendorCaller(
        router.vendor_credential_source(), async_transport=httpx.MockTransport(recorder)
    )
    session = WebRouterTools(router)
    yield session
    await session.aclose()


async def test_vendors_lists_vendors_then_endpoints(tools: WebRouterTools) -> None:
    observability = await tools.call(MCP_TOOL_VENDORS, {"category": "observability"})
    assert {row["vendor"]: row for row in observability["vendors"]}["datadog"]["configured"] is True
    advertising = await tools.call(MCP_TOOL_VENDORS, {"category": "advertising"})
    assert (
        "GOOGLE_ADS_REFRESH_TOKEN"
        in {row["vendor"]: row for row in advertising["vendors"]}["google-ads"]["set_to_enable"]
    )

    found = await tools.call(MCP_TOOL_VENDORS, {"query": "datadog monitors"})
    assert found["endpoints"][0]["endpoint"].startswith("datadog.monitors")
    assert found["endpoints"][0]["configured"] is True

    one_vendor = await tools.call(MCP_TOOL_VENDORS, {"vendor": "google-ads"})
    assert all(row["endpoint"].startswith("google-ads.") for row in one_vendor["endpoints"])


async def test_vendor_endpoint_describes_inputs_and_credentials(tools: WebRouterTools) -> None:
    described = await tools.call(MCP_TOOL_VENDOR_ENDPOINT, {"endpoint": "google-ads.query"})
    assert described["params"]["customer_id"]["in"] == "path"
    assert described["params"]["query"]["required"] is True
    assert any("MICROS" in note for note in described["notes"])
    assert described["credentials"]["developer_token"] == ["GOOGLE_ADS_DEVELOPER_TOKEN"]
    assert "GOOGLE_ADS_DEVELOPER_TOKEN" in described["set_to_enable"]


async def test_call_vendor_returns_the_answer_and_a_handle(tools: WebRouterTools, recorder: Recorder) -> None:
    result = await tools.call(MCP_TOOL_CALL_VENDOR, {"endpoint": "datadog.monitors.list", "params": {"name": "cpu"}})
    assert result["data"] == {"monitors": [{"id": 1, "name": "cpu"}]}
    assert result["truncated"] is False
    assert recorder.last.headers["DD-API-KEY"] == "dd-api"
    assert "dd-api" not in str(result)

    raw = await tools.call("fetch_raw", {"handle": result["handle"]})
    assert "monitors" in str(raw)


async def test_call_vendor_takes_a_raw_path(tools: WebRouterTools, recorder: Recorder) -> None:
    await tools.call(MCP_TOOL_CALL_VENDOR, {"vendor": "datadog", "method": "GET", "path": "/api/v1/org"})
    assert recorder.last.url.path == "/api/v1/org"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"endpoint": "datadog.monitors.list", "vendor": "datadog", "method": "GET", "path": "/x"},
        {"vendor": "datadog"},
    ],
)
async def test_call_vendor_needs_one_target(tools: WebRouterTools, arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="either 'endpoint'"):
        await tools.call(MCP_TOOL_CALL_VENDOR, arguments)


async def test_writes_need_the_server_switch_and_the_argument(
    tools: WebRouterTools, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {"vendor": "datadog", "method": "DELETE", "path": "/api/v1/monitor/1"}
    with pytest.raises(VendorWriteRefusedError, match=VENDOR_ENV_ALLOW_WRITES):
        await tools.call(MCP_TOOL_CALL_VENDOR, {**raw, "allow_writes": True})
    monkeypatch.setenv(VENDOR_ENV_ALLOW_WRITES, "1")
    with pytest.raises(VendorWriteRefusedError):
        await tools.call(MCP_TOOL_CALL_VENDOR, raw)
    assert recorder.requests == []

    await tools.call(MCP_TOOL_CALL_VENDOR, {**raw, "allow_writes": True})
    assert recorder.last.method == "DELETE"


def test_a_large_answer_is_cut_with_its_handle() -> None:
    response = VendorResponse(
        vendor="datadog",
        method="GET",
        url="https://api.datadoghq.com/x",
        status_code=200,
        data={"rows": ["x" * 50] * 500},
    )
    shaped = shape_vendor_response(response, "call_vendor-1", budget=2000)
    assert shaped["truncated"] is True
    assert "call_vendor-1" in shaped["note"]
    assert len(str(shaped)) < 3000


def test_load_env_accepts_vendor_variables() -> None:
    variables = credential_variables()
    assert {"DD_API_KEY", "DD_SITE", "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_LOGIN_CUSTOMER_ID"} <= variables


async def test_listings_fit_the_result_budget(tools: WebRouterTools) -> None:
    from web_router.mcp.shaping import max_response_chars, render

    listing = await tools.call(MCP_TOOL_VENDORS, {})
    assert len(render(listing)) <= max_response_chars()
    assert listing["vendor_count"] >= len(listing["vendors"]) > 0
    if len(listing["vendors"]) < listing["vendor_count"]:
        assert "narrow" in listing["note"]

    found = await tools.call(MCP_TOOL_VENDORS, {"query": "company", "limit": 200})
    assert len(render(found)) <= max_response_chars()

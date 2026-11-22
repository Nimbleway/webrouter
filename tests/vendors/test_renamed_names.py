"""The vendor-era names still work after the rename to data APIs: each warns once and does what its new name does."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router import AsyncWebRouter, WebRouter
from web_router.client_vendors import DataApiCredentials, VendorCredentials
from web_router.constants_data_api_compat import VENDOR_ENV_ALLOW_WRITES_OLD
from web_router.constants_vendors import VENDOR_ENV_ALLOW_WRITES
from web_router.constants_vendors_mcp import MCP_TOOL_RENAMED
from web_router.mcp.allowlist import allowed
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools
from web_router.mcp.vendor_tools import _log_old_writes_variable, writes_allowed
from web_router.vendors.caller import VendorCaller

DATADOG = {"datadog": {"api_key": "dd-api", "app_key": "dd-app"}}


def _wire(router: WebRouter | AsyncWebRouter, recorder: Recorder) -> None:
    router._vendor_caller = VendorCaller(
        router.vendor_credential_source(),
        transport=httpx.MockTransport(recorder),
        async_transport=httpx.MockTransport(recorder),
    )


@pytest.mark.parametrize(
    ("old", "new", "args"),
    [
        ("vendors", "data_apis", ()),
        ("vendor", "data_api", ("datadog",)),
        ("vendor_endpoint", "data_api_endpoint", ("datadog.monitors.list",)),
        ("find_vendor_endpoints", "find_data_api_endpoints", ("datadog error logs",)),
    ],
)
def test_old_lookup_names_warn_and_answer_like_the_new(old: str, new: str, args: tuple[Any, ...]) -> None:
    with WebRouter(router=None) as router:
        with pytest.warns(DeprecationWarning, match=f"`{old}` is deprecated.*use `{new}`"):
            answer = getattr(router, old)(*args)
        assert answer == getattr(router, new)(*args)


def test_old_call_names_warn_and_send_the_same_request() -> None:
    recorder = Recorder()
    with WebRouter(router=None, data_api_credentials=DATADOG) as router:
        _wire(router, recorder)
        with pytest.warns(DeprecationWarning, match="use `call_data_api`"):
            assert router.call_vendor("datadog.monitors.list").data == {"ok": True}
        with pytest.warns(DeprecationWarning, match="use `data_api_request`"):
            router.vendor_request("datadog", "GET", "/api/v2/users")
    assert [str(request.url.path) for request in recorder.requests] == ["/api/v1/monitor", "/api/v2/users"]


async def test_old_call_names_on_the_async_router() -> None:
    recorder = Recorder()
    async with AsyncWebRouter(router=None, data_api_credentials=DATADOG) as router:
        _wire(router, recorder)
        with pytest.warns(DeprecationWarning, match="use `call_data_api`"):
            await router.call_vendor("datadog.monitors.list")
        with pytest.warns(DeprecationWarning, match="use `data_api_request`"):
            await router.vendor_request("datadog", "GET", "/api/v2/users")
    assert len(recorder.requests) == 2


@pytest.mark.parametrize("router_class", [WebRouter, AsyncWebRouter])
def test_vendor_credentials_still_reaches_the_caller(router_class: type[WebRouter] | type[AsyncWebRouter]) -> None:
    with pytest.warns(DeprecationWarning, match="use `data_api_credentials`") as caught:
        router = router_class(router=None, vendor_credentials=DATADOG)
    assert caught[0].filename == __file__  # pointed at the caller, not at the router
    assert router.vendor_credential_source().get("datadog", "api_key", ()) == "dd-api"


def test_the_old_credentials_type_is_still_importable_where_it_was() -> None:
    from web_router.client import VendorCredentials as from_client

    assert VendorCredentials == from_client == DataApiCredentials


def test_the_writes_variable_keeps_its_old_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VENDOR_ENV_ALLOW_WRITES, raising=False)
    monkeypatch.delenv(VENDOR_ENV_ALLOW_WRITES_OLD, raising=False)
    assert not writes_allowed()
    monkeypatch.setenv(VENDOR_ENV_ALLOW_WRITES, "1")
    assert writes_allowed()
    monkeypatch.delenv(VENDOR_ENV_ALLOW_WRITES)
    monkeypatch.setenv(VENDOR_ENV_ALLOW_WRITES_OLD, "1")
    assert writes_allowed()


def test_the_old_writes_variable_is_logged_once_for_the_operator(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _log_old_writes_variable.cache_clear()
    monkeypatch.delenv(VENDOR_ENV_ALLOW_WRITES, raising=False)
    monkeypatch.setenv(VENDOR_ENV_ALLOW_WRITES_OLD, "1")
    with caplog.at_level(logging.WARNING):
        assert writes_allowed()
        assert writes_allowed()
    assert [record.getMessage() for record in caplog.records].count(
        f"`{VENDOR_ENV_ALLOW_WRITES_OLD}` is deprecated and will be removed: use `{VENDOR_ENV_ALLOW_WRITES}`."
        " Vendors are now data APIs."
    ) == 1


async def test_an_old_mcp_tool_name_runs_the_new_tool_with_a_note() -> None:
    tools = WebRouterTools(AsyncWebRouter(router=None, data_api_credentials=DATADOG))
    try:
        listed = {tool.name for tool in tools.list_tools()}
        assert set(MCP_TOOL_RENAMED.values()) <= listed
        assert not set(MCP_TOOL_RENAMED) & listed  # the old names are not listed
        result = await call_tool(tools, "vendors", {"category": "observability"})
        payload = json.loads(result.content[0].text)
        assert not result.is_error
        assert "use `data_apis`" in payload["deprecated"]
        assert "datadog" in {row["vendor"] for row in payload["vendors"]}
    finally:
        await tools.aclose()


async def test_a_failed_call_by_an_old_name_still_names_the_new_one() -> None:
    tools = WebRouterTools(AsyncWebRouter(router=None))
    try:
        result = await call_tool(tools, "call_vendor", {})
        assert result.is_error
        assert "use `call_data_api`" in result.content[0].text
    finally:
        await tools.aclose()


def test_an_allowlist_naming_old_tools_still_serves_them() -> None:
    handlers = dict.fromkeys(["search", *MCP_TOOL_RENAMED.values()], None)
    assert set(allowed(handlers, "search,call_vendor")) == {"search", "call_data_api"}


@pytest.mark.parametrize(
    ("value", "served"),
    [
        ("vendor*", {"data_apis", "data_api_endpoint"}),  # as before: `call_vendor` never matched it
        ("search,*vendor*", {"search", "data_apis", "data_api_endpoint", "call_data_api"}),
        ("*", {"search", "fetch_raw", "data_apis", "data_api_endpoint", "call_data_api"}),
        ("data_api*", {"data_apis", "data_api_endpoint"}),
    ],
)
def test_an_allowlist_pattern_written_for_the_old_names_still_serves_them(value: str, served: set[str]) -> None:
    handlers = dict.fromkeys(["search", "fetch_raw", *MCP_TOOL_RENAMED.values()], None)
    assert set(allowed(handlers, value)) == served

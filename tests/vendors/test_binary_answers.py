"""Vendor answers as they come back: binary bodies kept as bytes, and no secret left in the shown URL."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_vendors_mcp import MCP_TOOL_CALL_VENDOR
from web_router.mcp.tools import WebRouterTools
from web_router.vendors.caller import VendorCaller

_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x00\x01"
_DATADOG = {"datadog": {"api_key": "dd-test-key-1", "app_key": "dd-test-app-1"}}


def _answer(body: bytes, content_type: str | None) -> Recorder:
    headers = {"content-type": content_type} if content_type else {}
    return Recorder(lambda request: httpx.Response(200, content=body, headers=headers))


def _call(recorder: Recorder) -> Any:
    with WebRouter(vendor_credentials=_DATADOG) as router:
        router._vendor_caller = VendorCaller(router.vendor_credential_source(), transport=httpx.MockTransport(recorder))
        return router.call_vendor("datadog.monitors.list")


def test_a_binary_body_is_kept_as_bytes() -> None:
    response = _call(_answer(_PNG, "image/png"))
    assert response.content == _PNG
    assert response.data is None and response.text is None
    assert response.model_dump(mode="json")["content"] == base64.urlsafe_b64encode(_PNG).decode()


@pytest.mark.parametrize("content_type", ["text/csv; charset=utf-8", None])
def test_a_text_body_is_still_text(content_type: str | None) -> None:
    response = _call(_answer(b"a,b\n1,2\n", content_type))
    assert response.text == "a,b\n1,2\n"
    assert response.content is None


async def test_over_mcp_the_answer_names_the_size_and_fetch_raw_holds_the_bytes() -> None:
    router = AsyncWebRouter(vendor_credentials=_DATADOG)
    transport = httpx.MockTransport(_answer(_PNG, "image/png"))
    router._vendor_caller = VendorCaller(router.vendor_credential_source(), async_transport=transport)
    tools = WebRouterTools(router)
    try:
        shown = await tools.call(MCP_TOOL_CALL_VENDOR, {"endpoint": "datadog.monitors.list", "params": {}})
        raw = (await tools.call("fetch_raw", {"handle": shown["handle"]}))["raw"]
    finally:
        await tools.aclose()
    assert f"{len(_PNG)} bytes of image/png" in shown["data"]
    assert base64.urlsafe_b64decode(raw["content"]) == _PNG


def test_a_key_sent_as_an_ordinary_input_is_masked_in_the_shown_url() -> None:
    with WebRouter(vendor_credentials=_DATADOG) as router:
        transport = httpx.MockTransport(Recorder())
        router._vendor_caller = VendorCaller(router.vendor_credential_source(), transport=transport)
        response = router.vendor_request("datadog", "GET", "/api/v1/validate", query={"echo": "dd-test-key-1"})
    assert "dd-test-key-1" not in response.url

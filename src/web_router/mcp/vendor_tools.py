"""The three vendor tools: find a vendor endpoint, describe one, call one.

Kept out of :mod:`web_router.mcp.tools`, which already holds every other
tool, the way the template and vendor shaping live beside
:mod:`web_router.mcp.shaping`. Each is a thin projection of the router's
vendor methods; what is MCP's own is the write gate (the server's switch as
well as the call's) and how much of an answer fits.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from web_router.client import AsyncWebRouter
from web_router.constants import MCP_MSG_MISSING_ARGUMENT
from web_router.constants_vendors import (
    MCP_MSG_VENDOR_CALL_ARGS,
    MCP_TOOL_CALL_VENDOR,
    MCP_TOOL_VENDOR_ENDPOINT,
    MCP_TOOL_VENDORS,
    MSG_VENDOR_MCP_WRITE_REFUSED,
    VENDOR_ENV_ALLOW_WRITES,
)
from web_router.exceptions_vendors import VendorWriteRefusedError
from web_router.mcp.shaping import HandleStore, max_response_chars
from web_router.mcp.shaping_vendors import (
    shape_endpoint,
    shape_endpoint_list,
    shape_vendor_list,
    shape_vendor_response,
)
from web_router.vendors.catalog import get_catalog

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


async def vendors_tool(router: AsyncWebRouter, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Endpoints matching a query or one vendor's, best first; without either, the vendors themselves."""
    query, vendor = arguments.get("query"), arguments.get("vendor")
    if query or vendor:
        endpoints = router.find_vendor_endpoints(
            query or "", vendor=vendor, category=arguments.get("category"), limit=arguments.get("limit")
        )
        return shape_endpoint_list(
            endpoints, get_catalog(), router.vendor_credential_source(), budget=max_response_chars()
        )
    rows = router.vendors(
        category=arguments.get("category"), configured=arguments.get("configured"), data=arguments.get("data")
    )
    return shape_vendor_list(rows, budget=max_response_chars())


async def vendor_endpoint_tool(router: AsyncWebRouter, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One endpoint in full: its inputs, notes and credentials."""
    name = arguments.get("endpoint")
    if not name:
        raise ValueError(MCP_MSG_MISSING_ARGUMENT.format(tool=MCP_TOOL_VENDOR_ENDPOINT, name="endpoint"))
    endpoint = router.vendor_endpoint(name)
    vendor = router.vendor(endpoint.vendor)
    return shape_endpoint(endpoint, vendor, router.vendor_credential_source(), budget=max_response_chars())


async def call_vendor_tool(router: AsyncWebRouter, store: HandleStore, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """A catalog endpoint, or a raw path on a vendor's API; the vendor's answer, with a handle for the rest."""
    endpoint = arguments.get("endpoint")
    raw = [arguments.get(name) for name in ("vendor", "method", "path")]
    if not ((endpoint and not any(raw)) or (not endpoint and all(raw))):
        raise ValueError(MCP_MSG_VENDOR_CALL_ARGS)
    # Writes need both: the user started this server allowing them, and the model asked for this one.
    allow = arguments.get("allow_writes") is True and os.environ.get(VENDOR_ENV_ALLOW_WRITES) == "1"
    try:
        if endpoint:
            response = await router.call_vendor(endpoint, arguments.get("params"), allow_writes=allow)
        else:
            vendor, method, path = (str(value) for value in raw)
            response = await router.vendor_request(
                vendor,
                method,
                path,
                query=arguments.get("query"),
                body=arguments.get("body"),
                host=arguments.get("host"),
                allow_writes=allow,
            )
    except VendorWriteRefusedError as exc:
        message = MSG_VENDOR_MCP_WRITE_REFUSED.format(target=exc.target, vendor=exc.vendor)
        raise VendorWriteRefusedError(exc.vendor, exc.target, message) from None
    # The router already masked every credential the vendor echoed; fetch_raw redacts again on the way out.
    record = store.add(MCP_TOOL_CALL_VENDOR, raw=response.model_dump())
    return shape_vendor_response(response, record.handle, budget=max_response_chars())


async def _vendors(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return await vendors_tool(tools.router, arguments)


async def _vendor_endpoint(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return await vendor_endpoint_tool(tools.router, arguments)


async def _call_vendor(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return await call_vendor_tool(tools.router, tools.store, arguments)


TOOLS: dict[str, Callable[[WebRouterTools, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    MCP_TOOL_VENDORS: _vendors,
    MCP_TOOL_VENDOR_ENDPOINT: _vendor_endpoint,
    MCP_TOOL_CALL_VENDOR: _call_vendor,
}
"""The three vendor tools, each a handler taking the session's tools, as :class:`WebRouterTools` registers them."""

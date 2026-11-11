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

from web_router.constants import MCP_MSG_MISSING_ARGUMENT
from web_router.constants_vendors import VENDOR_ENV_ALLOW_WRITES
from web_router.constants_vendors_mcp import (
    MCP_MSG_VENDOR_ARG_TYPE,
    MCP_MSG_VENDOR_CALL_ARGS,
    MCP_TOOL_CALL_VENDOR,
    MCP_TOOL_VENDOR_ENDPOINT,
    MCP_TOOL_VENDORS,
    MSG_VENDOR_MCP_WRITE_REFUSED,
)
from web_router.exceptions_vendors import VendorWriteRefusedError
from web_router.mcp.shaping import max_response_chars, redact
from web_router.mcp.shaping_vendors import (
    shape_endpoint,
    shape_endpoint_list,
    shape_vendor_list,
    shape_vendor_response,
)

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools


_ARG_KINDS: dict[str, tuple[str, Callable[[Any], bool]]] = {
    "query": ("a string", lambda value: isinstance(value, str)),
    "vendor": ("a string", lambda value: isinstance(value, str)),
    "category": ("a string", lambda value: isinstance(value, str)),
    "limit": ("a whole number", lambda value: isinstance(value, int) and not isinstance(value, bool)),
    "configured": ("true or false", lambda value: isinstance(value, bool)),
}


def _check_types(arguments: Mapping[str, Any]) -> None:
    """A wrong type is the model's to fix, so it comes back as a readable tool error, not a crash."""
    for name, (kind, ok) in _ARG_KINDS.items():
        value = arguments.get(name)
        if value is not None and not ok(value):
            raise ValueError(MCP_MSG_VENDOR_ARG_TYPE.format(tool=MCP_TOOL_VENDORS, name=name, kind=kind, value=value))


async def vendors_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Endpoints matching a query or one vendor's, best first; without either, the vendors themselves."""
    _check_types(arguments)
    router = tools.router
    query, vendor = arguments.get("query"), arguments.get("vendor")
    if query or vendor:
        endpoints = router.find_vendor_endpoints(
            query or "", vendor=vendor, category=arguments.get("category"), limit=arguments.get("limit")
        )
        vendors = {name: router.vendor(name) for name in {endpoint.vendor for endpoint in endpoints}}
        return shape_endpoint_list(endpoints, vendors, router.vendor_credential_source(), budget=max_response_chars())
    rows = router.vendors(category=arguments.get("category"), configured=arguments.get("configured"))
    return shape_vendor_list(rows, budget=max_response_chars())


async def vendor_endpoint_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One endpoint in full: its inputs, notes and credentials."""
    router = tools.router
    name = arguments.get("endpoint")
    if not name:
        raise ValueError(MCP_MSG_MISSING_ARGUMENT.format(tool=MCP_TOOL_VENDOR_ENDPOINT, name="endpoint"))
    endpoint = router.vendor_endpoint(name)
    vendor = router.vendor(endpoint.vendor)
    return shape_endpoint(endpoint, vendor, router.vendor_credential_source(), budget=max_response_chars())


async def call_vendor_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """A catalog endpoint, or a raw path on a vendor's API; the vendor's answer, with a handle for the rest."""
    router = tools.router
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
    # The router masked every credential it sent; this also hides the session ids and tokens a vendor makes up,
    # before the answer is kept, so fetch_raw shows no more than the answer itself does.
    raw, _ = redact(response.model_dump())
    record = tools.store.add(MCP_TOOL_CALL_VENDOR, raw=raw)
    return shape_vendor_response(response, record.handle, budget=max_response_chars())


TOOLS: dict[str, Callable[[WebRouterTools, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    MCP_TOOL_VENDORS: vendors_tool,
    MCP_TOOL_VENDOR_ENDPOINT: vendor_endpoint_tool,
    MCP_TOOL_CALL_VENDOR: call_vendor_tool,
}
"""The three vendor tools, each a handler taking the session's tools, as :class:`WebRouterTools` registers them."""

"""The three data API tools: find a data API endpoint, describe one, call one.

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

from web_router.client_vendors_compat import warn_data_api_renamed
from web_router.constants import MCP_MSG_MISSING_ARGUMENT
from web_router.constants_vendors import MSG_DATA_API_DEPRECATED, VENDOR_ENV_ALLOW_WRITES, VENDOR_ENV_ALLOW_WRITES_OLD
from web_router.constants_vendors_mcp import (
    MCP_MSG_VENDOR_ARG_TYPE,
    MCP_MSG_VENDOR_CALL_ARGS,
    MCP_TOOL_CALL_VENDOR,
    MCP_TOOL_RENAMED,
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
        endpoints = router.find_data_apis(
            query or "", vendor=vendor, category=arguments.get("category"), limit=arguments.get("limit")
        )
        vendors = {name: router.data_api(name) for name in {endpoint.vendor for endpoint in endpoints}}
        return shape_endpoint_list(endpoints, vendors, router.vendor_credential_source(), budget=max_response_chars())
    rows = router.data_apis(category=arguments.get("category"), configured=arguments.get("configured"))
    return shape_vendor_list(rows, budget=max_response_chars())


async def vendor_endpoint_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One endpoint in full: its inputs, notes and credentials."""
    router = tools.router
    name = arguments.get("endpoint")
    if not name:
        raise ValueError(MCP_MSG_MISSING_ARGUMENT.format(tool=MCP_TOOL_VENDOR_ENDPOINT, name="endpoint"))
    endpoint = router.data_api_endpoint(name)
    vendor = router.data_api(endpoint.vendor)
    return shape_endpoint(endpoint, vendor, router.vendor_credential_source(), budget=max_response_chars())


def renamed_tool(name: str) -> tuple[str, str | None]:
    """The tool a data API tool's old name now means, and the note to add to its answer; ``name`` itself else.

    The old names are not listed, but a client that kept one still gets the answer, and is told.
    """
    new = MCP_TOOL_RENAMED.get(name)
    return (new, MSG_DATA_API_DEPRECATED.format(old=name, new=new)) if new else (name, None)


def writes_allowed() -> bool:
    """Whether the server was started allowing writes: the variable set to ``1``, or its old name, with a warning."""
    if os.environ.get(VENDOR_ENV_ALLOW_WRITES) == "1":
        return True
    if os.environ.get(VENDOR_ENV_ALLOW_WRITES_OLD) == "1":
        warn_data_api_renamed(VENDOR_ENV_ALLOW_WRITES_OLD, VENDOR_ENV_ALLOW_WRITES)
        return True
    return False


async def call_vendor_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """A catalog endpoint, or a raw path on a vendor's API; the vendor's answer, with a handle for the rest."""
    router = tools.router
    endpoint = arguments.get("endpoint")
    raw = [arguments.get(name) for name in ("vendor", "method", "path")]
    if not ((endpoint and not any(raw)) or (not endpoint and all(raw))):
        raise ValueError(MCP_MSG_VENDOR_CALL_ARGS)
    # Writes need both: the user started this server allowing them, and the model asked for this one.
    allow = arguments.get("allow_writes") is True and writes_allowed()
    try:
        if endpoint:
            response = await router.call_data_api(endpoint, arguments.get("params"), allow_writes=allow)
        else:
            vendor, method, path = (str(value) for value in raw)
            response = await router.data_api_request(
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
    raw, _ = redact(response.model_dump(mode="json"))
    record = tools.store.add(MCP_TOOL_CALL_VENDOR, raw=raw)
    return shape_vendor_response(response, record.handle, budget=max_response_chars())


TOOLS: dict[str, Callable[[WebRouterTools, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    MCP_TOOL_VENDORS: vendors_tool,
    MCP_TOOL_VENDOR_ENDPOINT: vendor_endpoint_tool,
    MCP_TOOL_CALL_VENDOR: call_vendor_tool,
}
"""The three data API tools, each a handler taking the session's tools, as :class:`WebRouterTools` registers them."""

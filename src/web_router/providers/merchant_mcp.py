"""The MCP exchange with a store, as pure builders and readers: the envelopes out, the product list back."""

from __future__ import annotations

import json
from typing import Any

import httpx

from web_router.constants import MCP_JSONRPC_VERSION
from web_router.constants_merchant import (
    CLIENT_NAME,
    CONTENT_TYPE_EVENT_STREAM,
    MCP_SESSION_HEADER,
    MSG_NO_PRODUCTS_FIELD,
    MSG_STORE_ERROR,
    PROVIDER_MERCHANT,
    TOOL_SEARCH,
    UCP_AGENT_META,
)
from web_router.exceptions import ProviderError
from web_router.types_errors import ERROR_KIND_RATE_LIMIT, ERROR_KIND_UPSTREAM
from web_router.types_merchant import MerchantInputs

__all__ = ["catalog", "envelope", "initialize", "negotiated_version", "products", "result_of", "search", "session"]


def envelope(call_id: int | None, method: str, params: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": MCP_JSONRPC_VERSION, "method": method, "params": params}
    if call_id is not None:
        message["id"] = call_id
    return message


def initialize(version: str) -> dict[str, Any]:
    params = {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": CLIENT_NAME, "version": "0"}}
    return envelope(1, "initialize", params)


def ensure_ok(response: httpx.Response) -> None:
    """A store that answers with an HTTP error stops the call; nothing after it is sent."""
    if response.status_code >= httpx.codes.BAD_REQUEST:
        kind = ERROR_KIND_RATE_LIMIT if response.status_code == httpx.codes.TOO_MANY_REQUESTS else ERROR_KIND_UPSTREAM
        raise ProviderError(PROVIDER_MERCHANT, f"HTTP {response.status_code}", kind=kind)


def _last_event_data(stream: str) -> str:
    """The data of the last server-sent event that has any: its ``data:`` lines, joined."""
    last = ""
    for event in stream.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        data = [line[5:].removeprefix(" ") for line in event.split("\n") if line.startswith("data:")]
        if data:
            last = "\n".join(data)
    return last


def result_of(response: httpx.Response) -> dict[str, Any]:
    """The ``result`` of an MCP reply, whether it came as plain JSON or as one server-sent event."""
    ensure_ok(response)
    text = response.text
    if response.headers.get("content-type", "").startswith(CONTENT_TYPE_EVENT_STREAM):
        text = _last_event_data(text)
    body = json.loads(text) if text.strip() else {}
    if isinstance(body, dict) and body.get("error"):
        raise ProviderError(PROVIDER_MERCHANT, str(body["error"])[:300], kind=ERROR_KIND_UPSTREAM)
    result = body.get("result") if isinstance(body, dict) else None
    return result if isinstance(result, dict) else {}


def negotiated_version(result: dict[str, Any], asked: str) -> str:
    """The protocol version the store agreed to in its ``initialize`` reply, else the one asked for."""
    version = result.get("protocolVersion")
    return version if isinstance(version, str) and version else asked


def session(response: httpx.Response) -> dict[str, str]:
    value = response.headers.get(MCP_SESSION_HEADER)
    return {MCP_SESSION_HEADER: value} if value else {}


def catalog(inputs: MerchantInputs) -> dict[str, Any]:
    """The ``catalog`` argument of ``search_catalog``: only the options the caller set."""
    body: dict[str, Any] = {"query": inputs.query}
    if inputs.limit is not None:
        body["pagination"] = {"limit": inputs.limit}
    price = {k: v for k, v in (("min", inputs.min_price), ("max", inputs.max_price)) if v is not None}
    if price:
        body["filters"] = {"price": price}
    context = {k: v for k, v in (("address_country", inputs.country), ("currency", inputs.currency)) if v is not None}
    if context:
        body["context"] = context
    return body


def search(inputs: MerchantInputs, profile_url: str | None, options: dict[str, Any]) -> dict[str, Any]:
    """The ``tools/call``. ``options`` come first so they can never replace ``catalog`` or ``meta``."""
    meta = {UCP_AGENT_META: {"profile": profile_url}} if profile_url else {}
    arguments = {**options, "meta": meta, "catalog": catalog(inputs)}
    return envelope(3, "tools/call", {"name": TOOL_SEARCH, "arguments": arguments})


def products(domain: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """The store's product records. A store error or an unreadable reply raises: never an empty list for either."""
    structured = result.get("structuredContent")
    if result.get("isError"):
        detail = json.dumps(structured)[:300] if structured else "tool error"
        raise ProviderError(
            PROVIDER_MERCHANT, MSG_STORE_ERROR.format(domain=domain, detail=detail), kind=ERROR_KIND_UPSTREAM
        )
    if isinstance(structured, dict) and isinstance(structured.get("products"), list):
        listed = structured["products"]
        kept = [p for p in listed if isinstance(p, dict)]
        if listed and not kept:  # a list of nothing readable is an unreadable reply, not "no matches"
            raise ProviderError(
                PROVIDER_MERCHANT, MSG_NO_PRODUCTS_FIELD.format(domain=domain), kind=ERROR_KIND_UPSTREAM
            )
        return kept
    raise ProviderError(PROVIDER_MERCHANT, MSG_NO_PRODUCTS_FIELD.format(domain=domain), kind=ERROR_KIND_UPSTREAM)

"""How a contract describes a provider's transport: an HTTP API's host and paths, an MCP tool, or an SDK.

Kept apart from :mod:`scripts.contracts`, which is at its size limit.
"""

from __future__ import annotations

from typing import Any


def transport(cls: type) -> dict[str, Any]:
    from web_router.providers._http import _HttpProvider
    from web_router.providers._mcp import McpSearchMixin

    # A backend over two of a vendor's APIs (Oxylabs' live data) records each one's wire.
    if apis := getattr(cls, "apis", ()):
        return {"kind": "apis", "apis": {api.__qualname__: transport(api) for api in apis}}
    if issubclass(cls, McpSearchMixin):
        return {"kind": "mcp", "url": cls.base_url, "tool": cls.mcp_tool}  # type: ignore[attr-defined]
    if not issubclass(cls, _HttpProvider):
        return {"kind": "sdk"}
    paths = {
        name: getattr(cls, name)
        for name in sorted(dir(cls))
        if name.endswith(("_path", "_url")) and name != "base_url" and isinstance(getattr(cls, name), str)
    }
    if not paths:
        raise SystemExit(f"{cls.__qualname__}: no endpoint; declare it as a *_path class attribute")
    wire: dict[str, Any] = {"kind": "http", "base_url": cls.base_url, "paths": paths}
    # The class verb is what a search or an extract is sent with; other calls choose theirs.
    if "search_path" in paths or "extract_path" in paths or "answer_path" in paths:
        wire["method"] = cls.http_method
    wire["auth_header"] = cls.api_key_header if cls.env_api_key else None
    return wire

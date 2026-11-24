"""An in-memory registry and store for the ``merchant`` backend: records requests, answers by URL and RPC method."""

from __future__ import annotations

import json
from typing import Any

import httpx

REGISTRY = "https://registry.test/agent-access/v1/"
SNAPSHOT = "a7a28f97d42f4bcf825a"
DOMAIN = "shop.example.com"
ENDPOINT = "https://store.myshopify.com/api/ucp/mcp"
PROFILE = "https://agent.example/agent-profile.json"
PRODUCT = {"id": "gid://p/1", "title": "Trail shoe", "price_range": {"min": {"amount": 9800, "currency": "USD"}}}


def pointer(**overrides: Any) -> dict[str, Any]:
    return {"schema_version": "1.0", "snapshot_id": SNAPSHOT, **overrides}


def record(tests: dict[str, str] | None = None, **overrides: Any) -> dict[str, Any]:
    """A domain record shaped like the published ones: one https route, profile-backed commerce evidence."""
    base: dict[str, Any] = {
        "schema_version": "1.0",
        "snapshot_id": SNAPSHOT,
        "domain": DOMAIN,
        "quarantined": False,
        "routes": [
            {
                "provider": "direct:ucp",
                "protocol": "mcp",
                "protocol_version": "2025-11-25",
                "endpoint": ENDPOINT,
                "cost_usd": None,
            }
        ],
        "capabilities": [{"name": "search_catalog", "production_ready": False}],
        "commerce_evidence": {
            "reached": True,
            "endpoint": ENDPOINT,
            "endpoint_matches_recorded_route": True,
            "real_catalogue": True,
            "needs_agent_profile": True,
            "observed_date": "2026-11-16",
            "tests": {"search_keyword": "pass", "price_filter": "pass", "localisation": "empty", **(tests or {})},
        },
    }
    return {**base, **overrides}


class Merchant:
    """The registry and the store behind one ``httpx.MockTransport``; ``seen`` lists (method, url, rpc method)."""

    def __init__(
        self,
        *,
        latest: dict[str, Any] | None = None,
        domain_record: dict[str, Any] | None = None,
        status: int = 200,
        store_result: dict[str, Any] | None = None,
        sse: bool = False,
        notice_status: int = 202,
    ) -> None:
        self.latest = pointer() if latest is None else latest
        self.domain_record = record() if domain_record is None else domain_record
        self.domain_status = status
        self.store_result = store_result if store_result is not None else {"structuredContent": {"products": [PRODUCT]}}
        self.sse = sse
        self.notice_status = notice_status
        self.index = {
            "sites": [
                {"domain": DOMAIN, "platform": "shopify", "commerce_tests": {"search_keyword": "pass"}},
                {"domain": "untested.example.com", "commerce_tests": {}},
            ]
        }
        self.seen: list[tuple[str, str, str | None]] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.seen.append((request.method, str(request.url), body.get("method")))
        self.bodies.append(body)
        self.headers.append(request.headers)
        if str(request.url) == REGISTRY + "latest.json":
            return httpx.Response(200, json=self.latest)
        if str(request.url) == REGISTRY + f"snapshots/{SNAPSHOT}/index.json":
            return httpx.Response(200, json=self.index)
        if str(request.url).startswith(REGISTRY + "snapshots/"):
            return httpx.Response(self.domain_status, json=self.domain_record)
        if str(request.url) == ENDPOINT:
            return self._store(body)
        return httpx.Response(404)

    def _store(self, body: dict[str, Any]) -> httpx.Response:
        method = body.get("method")
        if method == "notifications/initialized":
            return httpx.Response(self.notice_status)
        result: dict[str, Any] = {"protocolVersion": "2025-11-25"} if method == "initialize" else self.store_result
        reply = {"jsonrpc": "2.0", "id": body.get("id"), "result": result}
        headers = {"mcp-session-id": "s-1"}
        if self.sse and method == "tools/call":
            return httpx.Response(
                200,
                text=f"event: message\ndata: {json.dumps(reply)}\n\n",
                headers={**headers, "content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=reply, headers=headers)

    def store_calls(self) -> list[str | None]:
        return [rpc for _, url, rpc in self.seen if url == ENDPOINT]

    def tool_call(self) -> dict[str, Any]:
        return next(b for b in self.bodies if b.get("method") == "tools/call")["params"]


def router_config(merchant: Merchant, **options: Any) -> dict[str, dict[str, Any]]:
    """``provider_config`` pointing a router's merchant backend at ``merchant``; one transport serves both clients."""
    merged: dict[str, Any] = {
        "registry_url": REGISTRY,
        "agent_profile_url": PROFILE,
        "transport": merchant.transport,
        **options,
    }
    return {"merchant": merged, "nimble": {"api_key": "k"}}

"""Constants for the ``merchant`` live data backend: a store's own agent interface, found through a hosted registry."""

from __future__ import annotations

import re

PROVIDER_MERCHANT = "merchant"
"""The backend's name. Its sources are store domains."""
ENV_MERCHANT_AGENT_PROFILE_URL = "WEB_ROUTER_MERCHANT_AGENT_PROFILE_URL"
"""The caller's agent profile URL. It is the setting that makes the backend usable without being named."""

REGISTRY_URL = "https://shopping.nimbleway.com/agent-access/v1/"
"""Where the registry is published. Overridable: a caller may mirror it, and a release is written against one schema."""
REGISTRY_LATEST = "latest.json"
REGISTRY_INDEX_PATH = "snapshots/{snapshot_id}/index.json"
REGISTRY_DOMAIN_PATH = "snapshots/{snapshot_id}/domains/{domain}.json"
REGISTRY_SCHEMA_VERSION = "1.0"
REGISTRY_MAX_BYTES = 8 * 1024 * 1024
REGISTRY_POINTER_TTL_S = 60.0
"""How long the version pointer is trusted before it is read again; records are cached per snapshot for good."""
SNAPSHOT_ID = re.compile(r"^[0-9a-f]{8,64}$")
DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")

ROUTE_PROVIDER = "direct:ucp"
ROUTE_PROTOCOL = "mcp"
ROUTE_SCHEME = "https"
PLATFORM_HOST_SUFFIXES = (".myshopify.com", ".wixapis.com")
"""Hosts a store's endpoint may be on although they are not the store's own domain: the platforms that run it."""
TOOL_SEARCH = "search_catalog"
UCP_AGENT_META = "ucp-agent"
CLIENT_NAME = "web-router-merchant"
MCP_SESSION_HEADER = "mcp-session-id"
MCP_VERSION_HEADER = "MCP-Protocol-Version"
PROTOCOL_VERSION_FALLBACK = "2025-11-25"
CONTENT_TYPE_EVENT_STREAM = "text/event-stream"
RESERVED_OPTIONS = frozenset({"catalog", "meta"})
"""Tool arguments ``provider_options`` may not set: ``catalog`` carries the gated options and ``meta`` the identity."""

EVIDENCE_PASS = "pass"
TEST_SEARCH = "search_keyword"
TEST_PRICE = "price_filter"
TEST_LOCALISATION = "localisation"
GATES = {"min_price": TEST_PRICE, "max_price": TEST_PRICE, "country": TEST_LOCALISATION, "currency": TEST_LOCALISATION}

MSG_REGISTRY_UNREADABLE = "the registry at {url} could not be read: {reason}"
MSG_REGISTRY_INVALID = "the registry answered with something this client does not accept: {reason}"
MSG_NO_ROUTE = "{domain} has no runnable {provider} route in snapshot {snapshot_id}: {reason}"
MSG_NOT_VALIDATED = (
    "{domain} has no passing {test} evidence for {what}, so the call is refused rather than run without it "
    "(recorded status: {status})."
)
MSG_NEEDS_PROFILE = (
    "{domain} requires the caller's agent profile URL: pass "
    "provider_config={{'merchant': {{'agent_profile_url': ...}}}} or set " + ENV_MERCHANT_AGENT_PROFILE_URL + "."
)
MSG_REDIRECTS_REFUSED = (
    "Provider 'merchant' refuses follow_redirects=True: the route's host is checked before the call, and a "
    "redirect (307, 308) would send the search and the caller's agent profile URL to a host that was never checked."
)
MSG_STORE_ERROR = "{domain} answered the call with an error: {detail}"
MSG_NO_PRODUCTS_FIELD = "{domain} answered, but its reply carries no product list this client can read"
MSG_BAD_INPUTS = "inputs for {domain} are not valid: {reason}"
MSG_RESERVED_OPTION = "provider_options may not set {keys}: they carry the gated options and the caller's identity"
MSG_HOST_REFUSED = "the route's host {host} is not {domain}, a subdomain of it, or a platform host this client trusts"

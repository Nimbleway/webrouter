"""AWS AgentCore Web Search: the gateway path, signing, limits and messages.

Kept apart from :mod:`web_router.constants`, as :mod:`web_router.constants_deepseek`
is, so that file stops growing. The env var and the provider's place in ``auto``
stay there, beside every other backend's.
"""

from __future__ import annotations

# Amazon's web search, reached only through an AgentCore Gateway the caller
# creates in their own AWS account, as the `web-search` connector target. The
# gateway speaks MCP over streamable HTTP and takes a lone `tools/call` without
# `initialize`; requests are SigV4-signed for `bedrock-agentcore` in the
# gateway's region, which its hostname carries. Documented at
# docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html.

AGENTCORE_DEFAULT_PATH = "/mcp"
"""Where a gateway serves MCP: ``CreateGateway`` returns a ``gatewayUrl`` ending in it."""

AGENTCORE_SIGNING_SERVICE = "bedrock-agentcore"
AGENTCORE_UNSIGNED_HEADER = "connection"
"""Left out of the signature: the gateway does not include it in the one it checks, so signing
httpx's keep-alive header would fail every request (as AWS's own mcp-proxy-for-aws notes)."""
AGENTCORE_HOST_PATTERN = r"^[^.]+\.gateway\.bedrock-agentcore\.(?P<region>[a-z0-9-]+)\.amazonaws\.com$"
"""A gateway's hostname, whose region is the one its requests are signed for."""

AGENTCORE_TOOL_SUFFIX = "___WebSearch"
"""The gateway names every tool ``<target name>___<tool>``, so the search tool is found by its suffix."""

AGENTCORE_MAX_QUERY_CHARS = 200
AGENTCORE_MAX_RESULTS = 25
AGENTCORE_PARAM_NAMES = {"max_results": "maxResults"}
AGENTCORE_DOMAIN_FIELDS = {"include_domains": "include", "exclude_domains": "exclude"}
"""Under ``filters.domainFilter``, which the connector accepts from version 1.2.0."""

AGENTCORE_FILTERS_FIELD = "filters"
AGENTCORE_DOMAIN_FILTER_FIELD = "domainFilter"
AGENTCORE_DATE_FILTER_FIELD = "publishedDateFilter"
AGENTCORE_DATE_FROM_FIELD = "from"
AGENTCORE_DATE_FORMAT = "%Y-%m-%dT00:00:00Z"
"""``publishedDateFilter.from`` is an inclusive ISO-8601 UTC date-time; ``recency`` sets it to its window's first day.

The form a live date-range search used (2026-10-26, through ``provider_options``);
``recency`` itself has not been run against a gateway.
"""
AGENTCORE_SETUP_DOC = "docs/agentcore-setup.md"

MSG_AGENTCORE_BAD_URL = (
    "AGENTCORE_GATEWAY_URL must be a gateway's https URL, "
    "https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp; got {url!r}. See {doc}."
)
MSG_AGENTCORE_NO_REGION = (
    "Cannot tell which AWS region gateway {host!r} is in. Use the gateway URL AWS returned, "
    "or set AWS_REGION. See {doc}."
)
MSG_AGENTCORE_NO_CREDENTIALS = (
    "No AWS credentials found for agentcore. Configure a profile, a role or the AWS_* variables "
    "botocore reads. See {doc}."
)
MSG_AGENTCORE_CURSOR_REPEATED = "The gateway's tool list repeated the page cursor {cursor!r}; stopped listing."
MSG_AGENTCORE_TOOL = (
    "Gateway has {count} tools ending in {suffix!r} ({names}); agentcore needs exactly one. "
    "Add the web-search connector as a target. See {doc}."
)
MSG_AGENTCORE_NO_FILTERS = (
    "This gateway's web search target takes no filters, so {names} cannot be sent. "
    "Pin the target to connector version 1.2.0 or later. See {doc}."
)
MSG_AGENTCORE_QUERY_TOO_LONG = "AgentCore Web Search takes queries of up to {limit} characters; this one has {length}."
MSG_AGENTCORE_TOO_MANY_RESULTS = (
    "AgentCore Web Search returns at most {limit} results; max_results={value} was asked for."
)
MSG_AGENTCORE_FILTER_CLASH = (
    "provider_options['filters'] sets {keys}, which include_domains, exclude_domains or recency already set. "
    "Set each once."
)

ENV_AWS_REGION = "AWS_REGION"
"""Read after the gateway's hostname: botocore's own region setting reads only
AWS_DEFAULT_REGION and the profile, and this is the name the SDKs and the CLI also honour."""

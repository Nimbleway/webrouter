"""The server's resources: the one it offers, how it is read, and the empty template list."""

from __future__ import annotations

from mcp.shared.exceptions import MCPError
from mcp_types import (
    ListResourcesResult,
    ListResourceTemplatesResult,
    ReadResourceResult,
    Resource,
    TextResourceContents,
)

from web_router.constants import (
    MCP_MIME_JSON,
    MCP_MSG_UNKNOWN_RESOURCE,
    MCP_RESOURCE_DESCRIPTION,
    MCP_RESOURCE_NAME,
    MCP_RESOURCE_TITLE,
    MCP_RESOURCE_URI,
)
from web_router.constants_mcp import MCP_RESOURCE_NOT_FOUND
from web_router.mcp.shaping import render
from web_router.mcp.tools import WebRouterTools


def list_resources() -> ListResourcesResult:
    """The one resource: what this install can actually do."""
    return ListResourcesResult(
        resources=[
            Resource(
                uri=MCP_RESOURCE_URI,
                name=MCP_RESOURCE_NAME,
                title=MCP_RESOURCE_TITLE,
                description=MCP_RESOURCE_DESCRIPTION,
                mime_type=MCP_MIME_JSON,
            )
        ]
    )


def read_resource(tools: WebRouterTools, uri: str) -> ReadResourceResult:
    """The configured providers per method, so a client learns them without a round trip."""
    if uri != MCP_RESOURCE_URI:
        # The spec's resource-not-found code: anything else reaches the client as an internal error.
        raise MCPError(MCP_RESOURCE_NOT_FOUND, MCP_MSG_UNKNOWN_RESOURCE.format(uri=uri, known=MCP_RESOURCE_URI))
    return ReadResourceResult(
        contents=[
            TextResourceContents(uri=uri, mime_type=MCP_MIME_JSON, text=render(tools.capabilities())),
        ]
    )


async def list_resource_templates() -> ListResourceTemplatesResult:
    """None to offer, but a client that asks is answered rather than told the method does not exist."""
    return ListResourceTemplatesResult(resource_templates=[])

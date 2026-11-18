"""The MCP server's HTTP transport constants: the command-line choices, the bind defaults and the token.

Apart from ``constants.py`` so that file stops growing, and free of the ``mcp`` SDK so the console script can
read them before it knows the SDK is installed.
"""

from __future__ import annotations

MCP_TRANSPORT_STDIO = "stdio"
MCP_TRANSPORT_HTTP = "http"
MCP_TRANSPORTS = (MCP_TRANSPORT_STDIO, MCP_TRANSPORT_HTTP)

MCP_HTTP_DEFAULT_HOST = "127.0.0.1"
"""Loopback, so a server started without ``--host`` is reachable from this machine only."""

MCP_HTTP_DEFAULT_PORT = 8000
MCP_HTTP_PATH = "/mcp"
MCP_HTTP_LOCALHOST = "localhost"

ENV_MCP_TOKEN = "WEB_ROUTER_MCP_TOKEN"
"""The bearer token every HTTP request must carry; required to listen on anything but loopback."""

MCP_HTTP_BEARER = "bearer"

MCP_MSG_HTTP_NEEDS_TOKEN = (
    "Refusing to listen on {host} without a token: anyone who can reach that address could spend your keys. "
    "Set {env} to a long random secret, and have clients send it as 'Authorization: Bearer <token>'. "
    "Only a loopback address (127.0.0.1, ::1, localhost) may run without one."
)

MCP_MSG_HTTP_UNAUTHORIZED = "Missing or wrong bearer token."

MCP_MSG_HTTP_STDIO_FLAGS = "--host and --port apply to --transport http only."

MCP_CLI_DESCRIPTION = "Run the web-router MCP server over stdio (the default) or streamable HTTP."

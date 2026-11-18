"""The ``web-router-mcp`` console script: the MCP server, or the install line when its extra is missing.

It lives outside :mod:`web_router.mcp` because importing that package imports
the ``mcp`` SDK, so a missing SDK would fail before any code here could name
the fix. ``pip install "webrouter[all]"`` installs the server's extra; a narrower
install may not.

``--transport http`` serves streamable HTTP instead of stdio; see
:mod:`web_router.mcp.http_transport`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from web_router.constants import EXTRA_MCP
from web_router.constants_mcp_http import (
    MCP_CLI_DESCRIPTION,
    MCP_CLI_HELP_HOST,
    MCP_CLI_HELP_PORT,
    MCP_HTTP_DEFAULT_HOST,
    MCP_HTTP_DEFAULT_PORT,
    MCP_HTTP_PORT_RANGE,
    MCP_MSG_HTTP_BAD_PORT,
    MCP_MSG_HTTP_STDIO_FLAGS,
    MCP_TRANSPORT_HTTP,
    MCP_TRANSPORT_STDIO,
    MCP_TRANSPORTS,
)
from web_router.constants_providers import MSG_EXTRA_MISSING

# What the server imports that only its extra installs.
_MCP_EXTRA_MODULES = frozenset({"mcp", "mcp_types", "dotenv", "anyio", "uvicorn", "starlette"})


def _port(value: str) -> int:
    low, high = MCP_HTTP_PORT_RANGE
    if not value.isdigit() or not low <= int(value) <= high:
        raise argparse.ArgumentTypeError(MCP_MSG_HTTP_BAD_PORT.format(low=low, high=high, value=value))
    return int(value)


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="web-router-mcp", description=MCP_CLI_DESCRIPTION)
    parser.add_argument("--transport", choices=MCP_TRANSPORTS, default=MCP_TRANSPORT_STDIO)
    parser.add_argument("--host", help=MCP_CLI_HELP_HOST.format(default=MCP_HTTP_DEFAULT_HOST))
    parser.add_argument("--port", type=_port, help=MCP_CLI_HELP_PORT.format(default=MCP_HTTP_DEFAULT_PORT))
    args = parser.parse_args(argv)
    if args.transport == MCP_TRANSPORT_STDIO and (args.host is not None or args.port is not None):
        parser.error(MCP_MSG_HTTP_STDIO_FLAGS)
    return args


def main(argv: Sequence[str] | None = None) -> None:
    """Run the server; with its extra missing, print how to install it and exit with status 1."""
    args = _arguments(argv)
    try:
        if args.transport == MCP_TRANSPORT_HTTP:
            from web_router.mcp.http_transport import main as serve
        else:
            from web_router.mcp.server import main as serve_stdio
    except ModuleNotFoundError as exc:
        top = (exc.name or "").split(".")[0]
        if top not in _MCP_EXTRA_MODULES:
            raise
        print(MSG_EXTRA_MISSING.format(what="The MCP server", module=top, extra=EXTRA_MCP), file=sys.stderr)
        raise SystemExit(1) from None
    if args.transport == MCP_TRANSPORT_HTTP:
        serve(args.host or MCP_HTTP_DEFAULT_HOST, MCP_HTTP_DEFAULT_PORT if args.port is None else args.port)
    else:
        serve_stdio()

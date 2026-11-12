"""The ``web-router-mcp`` console script: the MCP server, or the install line when its extra is missing.

It lives outside :mod:`web_router.mcp` because importing that package imports
the ``mcp`` SDK, so a missing SDK would fail before any code here could name
the fix. ``pip install "webrouter[all]"`` installs the server's extra; a narrower
install may not.
"""

from __future__ import annotations

import sys

from web_router.constants import EXTRA_MCP
from web_router.constants_providers import MSG_EXTRA_MISSING

# What the server imports that only its extra installs.
_MCP_EXTRA_MODULES = frozenset({"mcp", "mcp_types", "dotenv", "anyio"})


def main() -> None:
    """Run the server; with its extra missing, print how to install it and exit with status 1."""
    try:
        from web_router.mcp.server import main as serve_stdio
    except ModuleNotFoundError as exc:
        top = (exc.name or "").split(".")[0]
        if top not in _MCP_EXTRA_MODULES:
            raise
        print(MSG_EXTRA_MISSING.format(what="The MCP server", module=top, extra=EXTRA_MCP), file=sys.stderr)
        raise SystemExit(1) from None
    serve_stdio()

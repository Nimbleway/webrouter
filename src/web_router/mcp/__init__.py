"""A self-hosted stdio MCP server over web-router's search, extract, research and browse.

Run it with ``web-router-mcp`` and point an MCP client -- Claude Code, Claude
Desktop, Cursor -- at that command. It runs in your own process and reads your
own credentials from your own environment, so no key ever leaves the machine;
with no key at all the ``auto`` chain still searches and extracts through the
keyless backends.

Seventeen tools: ``search`` and ``extract`` answer in about a second, ``research``
starts an agentic run and hands back a handle, ``research_status`` polls it,
``browse`` and ``browse_status`` do the same for a hosted browser agent's task,
and ``fetch_raw`` returns the untouched payload behind any shaped result --
redacted, for a browse task, of the session it ran in. ``load_env`` loads keys
from a ``.env`` file the user names, without a restart. The nine ``browser_*``
tools let the model drive a browser itself: ``browser_open`` hands back an id
the server minted, and the server holds the session, its CDP URL and its
connection until ``browser_close``, an idle timeout or shutdown releases it.

    $ make setup   # in your webrouter clone: every extra
    $ uv run web-router-mcp

At startup the server loads keys from ``WEB_ROUTER_ENV_FILE``, a ``.env`` in its
working directory, or one in the user config directory -- see
:mod:`web_router.mcp.env`. See docs/mcp.mdx (docs.webrouter.net/mcp) for the client configuration block.
"""

from web_router.mcp.browsers import BrowserSessions
from web_router.mcp.env import EnvSources
from web_router.mcp.server import build_server, main, serve
from web_router.mcp.shaping import HandleStore
from web_router.mcp.tools import WebRouterTools

__all__ = ["BrowserSessions", "EnvSources", "HandleStore", "WebRouterTools", "build_server", "main", "serve"]

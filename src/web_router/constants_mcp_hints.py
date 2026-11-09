"""Each MCP tool's title and behaviour hints, kept apart from :mod:`web_router.constants_mcp` so neither file grows."""

from __future__ import annotations

MCP_TOOL_HINTS: dict[str, tuple[str, bool, bool, bool, bool]] = {
    # name: (title, read-only, destructive, idempotent, open world), as MCP's ToolAnnotations reads them.
    # Read-only means it changes nothing the user owns, not that it is free: a billed lookup is read-only.
    "search": ("Search the web", True, False, True, True),
    "extract": ("Read pages by URL", True, False, True, True),
    "templates": ("List Extract Templates", True, False, True, True),
    "template": ("Describe an Extract Template", True, False, True, True),
    "run_template": ("Run an Extract Template", True, False, True, True),
    "answer": ("Answer a question", True, False, True, True),
    "research": ("Start a research run (billed)", False, False, False, True),
    "research_status": ("Poll a research run", True, False, True, False),
    "browse": ("Start a browser-agent task (billed)", False, True, False, True),
    "browse_status": ("Poll a browser-agent task", True, False, True, False),
    "fetch_raw": ("Fetch a result's full payload", True, False, True, False),
    "load_env": ("Load API keys from a .env file", False, False, True, False),
    "vendors": ("Find a vendor API endpoint", True, False, True, False),
    "vendor_endpoint": ("Describe a vendor API endpoint", True, False, True, False),
    # call_vendor can write to the user's vendor account, but only when the server and the call both allow it.
    "call_vendor": ("Call a vendor's API with your credentials", False, True, False, True),
    # web_run can start any tool it routes to, a browse task included, so it is as cautious as the most cautious.
    "web_run": ("Route and run a request", False, True, False, True),
    "budget_start": ("Start a task budget", False, False, False, False),
    "budget_status": ("Show the budget", True, False, True, False),
    "browser_open": ("Open a browser session", False, False, False, True),
    "browser_goto": ("Load a URL in the browser", False, True, False, True),
    "browser_snapshot": ("Read the page's element tree", True, False, True, True),
    "browser_click": ("Click an element", False, True, False, True),
    "browser_type": ("Type into a field", False, True, False, True),
    "browser_press": ("Press a key", False, True, False, True),
    "browser_screenshot": ("Screenshot the page", True, False, True, True),
    "browser_read": ("Read the page's text", True, False, True, True),
    "browser_close": ("Close a browser session", False, False, True, True),
}
"""Each tool's title and behaviour hints, so a client can tell a free lookup from a billed run or a page action.

Keyed by the tools' wire names; a test holds the keys to the tools the server lists.
"""

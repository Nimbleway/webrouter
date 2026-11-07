"""The MCP server's instructions, and the constants of its routing tools: ``web_run`` and the budget.

Kept apart from :mod:`web_router.constants`, so that file stops growing; it
imports the tool tables from here. Experimental, as routing is.
"""

from __future__ import annotations

from typing import Any, Literal

MCP_SERVER_INSTRUCTIONS = (
    "Search, read and research the web through whichever backends this machine holds keys for. "
    "`search` and `extract` answer in about a second, and so does `run_template`, which runs a "
    "backend's Extract Template -- a saved, site-specific extraction that turns one page into a "
    "record; `templates` lists them and `template` describes one, inputs and all. `answer` returns a "
    "backend's own written answer to one question, with its sources, in a few seconds. `research` runs an "
    "agentic job that takes "
    "minutes and bills for the run, and `browse` hands a task to a hosted browser agent on the same "
    "terms. The `browser_*` tools let you drive a browser yourself: `browser_open` hands back a "
    "session_id, the other tools act through it, and `browser_close` ends it. "
    "`web_run` takes a request in words and picks the method and backends itself; `budget_start` "
    "sets a cost and time budget every billed tool then spends from, and `budget_status` shows it. "
    "When a lookup ends at a page that needs interaction -- a login, a form, a multi-step portal, a "
    "result gated on the user's own details -- do not stop at describing the steps: tell the user a "
    "browser could finish the task and ask before opening one. For a page that needs the user's own "
    "credentials, offer `browser_open` with provider='local' and headless=false, a visible window on "
    "their machine where they type their details themselves; never ask for a password, an ID number or "
    "a date of birth in the conversation. "
    "Results are shortened to fit a context window -- every one "
    "carries a handle, and `fetch_raw` returns the backend's untouched payload for it. "
    "Read the web-router://capabilities resource to see which backends are configured here, and "
    "`load_env` to load API keys from a .env file the user names. "
    "Every backend key the user adds unlocks more: more backends, more fallbacks and, for research, "
    "browse and cloud browsers, methods that need a key at all -- with a key for every backend, every "
    "capability is available. The resource's `unlock` lists, per method, each backend still missing a "
    "key and the variable that would add it; when a method has nothing to run on, or the user wants "
    "more, point them there and to .env.example, and have them put keys in their .env themselves."
)


ENV_MCP_MAX_COST_USD = "WEB_ROUTER_MCP_MAX_COST_USD"
"""The operator's ceiling on what one connection may spend, in US dollars at estimated list prices."""

ENV_MCP_TIME_LIMIT_S = "WEB_ROUTER_MCP_TIME_LIMIT_S"
"""The operator's ceiling on how long one connection may run billed calls, in seconds from its start."""

ENV_MCP_ROUTER = "WEB_ROUTER_MCP_ROUTER"
"""``<module>:<attribute>``, the router ``web_run`` plans with; ``off`` for none. The built-in rules router when unset.

The three are read once, when the server starts, from the process environment
only: no ``.env`` file and no ``load_env`` call can set or change them.
"""

MCP_ROUTER_OFF = "off"
"""The value of :data:`ENV_MCP_ROUTER` that attaches no router, and how the capabilities resource names it."""

MCP_URL_POLICY: Literal["public_only"] = "public_only"
"""The ``url_policy`` ``web_run``'s plans hold to: a prompt cannot steer a locally fetching backend at private hosts.

It holds for ``web_run``'s plans only. The direct ``extract`` and ``browse``
tools send the URLs the agent passes as they are, and the browser tools keep
their own guard (:mod:`web_router.mcp.navigation`), which checks DNS answers
and redirects but refuses only cloud metadata hosts and the browser's own
debugging port: other loopback and private hosts are admitted.
"""

MCP_URL_POLICY_NOTE = (
    "Holds for web_run's plans only. extract and browse send the URLs passed to them as they are; the "
    "browser tools have their own guard, which refuses cloud metadata hosts and the browser's debugging "
    "port but admits other loopback and private hosts."
)
"""What the capabilities resource says of ``url_policy``: where it holds, and where it does not."""

MCP_ROUTER_IN_PROCESS = "in_process"
"""Where ``web_run``'s router runs with the built-in rules router or none: in this process, sending nothing out."""

MCP_ROUTER_OPERATOR = "operator"
MCP_ROUTER_OPERATOR_NOTE = (
    "The operator's router runs in this process, but it is the operator's code: it may send the prompt "
    "elsewhere, such as to a model's server."
)
"""Where an operator's router runs, and what that means for the prompt."""

MCP_PROMPT_NOTE = "web_run sends the prompt to the backend that serves it, as its query or task, as search does."
"""What the capabilities resource says of the prompt whatever the router: a backend always receives it."""

MCP_TOOL_WEB_RUN = "web_run"
MCP_TOOL_BUDGET_START = "budget_start"
MCP_TOOL_BUDGET_STATUS = "budget_status"

_MCP_COST_PROPERTY: dict[str, Any] = {
    "type": "number",
    "exclusiveMinimum": 0,
    "description": "The most to spend, in US dollars, at estimated list prices.",
}
_MCP_TIME_PROPERTY: dict[str, Any] = {
    "type": "number",
    "exclusiveMinimum": 0,
    "description": "The most seconds to wait.",
}

MCP_WEB_RUN_PROPERTIES: dict[str, dict[str, Any]] = {
    "prompt": {"type": "string", "description": "What to find or read, in words; a URL in it is read as one to fetch."},
    "max_cost_usd": {
        **_MCP_COST_PROPERTY,
        "description": "Caps this call, within the session's budget, in US dollars.",
    },
    "time_limit_s": {**_MCP_TIME_PROPERTY, "description": "Caps this call's wait, within the session's budget."},
    "dry_run": {
        "type": "boolean",
        "default": False,
        "description": "Return the plan -- method, options, backend order, decision record -- without running it.",
    },
}
MCP_BUDGET_START_PROPERTIES: dict[str, dict[str, Any]] = {
    "max_cost_usd": _MCP_COST_PROPERTY,
    "time_limit_s": {**_MCP_TIME_PROPERTY, "description": "The most seconds the task may run billed calls, from now."},
}

MCP_DESC_WEB_RUN = (
    "Hand a request in plain words to web-router's router, which picks the method and the backends and "
    "runs it: by default a prompt naming a URL is read (`extract`) and anything else is searched (`search`). "
    "Use it when you would rather not choose. Call `search` or `extract` directly when you know which you "
    "want or need their options -- `max_results`, `format`, a `provider` -- since this takes only the "
    "prompt and a cap. `dry_run=true` returns the plan -- the method, its options, the backend order and "
    "the decision record -- without running or billing anything. `max_cost_usd` and `time_limit_s` cap "
    "this one call, inside the session's budget. The result is shaped like the method's own, with a "
    "handle for `fetch_raw`, the method that ran and the decision record."
)

MCP_DESC_BUDGET_START = (
    "Start a new task budget for the calls that follow: `max_cost_usd` dollars at estimated list prices, "
    "and `time_limit_s` seconds from now. Every billed tool -- search, extract, answer, research, browse, "
    "run_template, browser_open and web_run -- spends from it, and a call that does not fit is refused "
    "with BudgetExceededError before anything bills. State both limits each time: the previous task's "
    "budget closes, and a call still running stays charged. The operator's ceiling for this connection, "
    "if one is set, still caps the task: the limits are cut to what the ceiling has left, and no call can "
    "raise it. Under a cost cap, a backend whose price is unknown never runs."
)

MCP_DESC_BUDGET_STATUS = (
    "Where the current budget stands: dollars spent, reserved for calls still running and left, and "
    "seconds elapsed and left; and the operator's ceiling, if one is set. Free: it calls nothing."
)

MCP_MSG_ROUTER_SPEC = "{env_var}={value!r} names no router: expected <module>:<attribute>, or 'off'."
MCP_MSG_ROUTER_IMPORT = "{env_var}={value!r}: {error}"
MCP_MSG_CEILING_VALUE = "{env_var}={value!r} is not a positive number."
MCP_MSG_BUDGET_LIMIT = "{tool}: `{name}` must be a positive number, got {value!r}."

MCP_PRICE_RUN_TEMPLATE = "run_template"
MCP_PRICE_OPEN_BROWSER = "open_browser"
"""The price list's methods for the two billed tools no ``auto`` hooks, which the server charges itself."""

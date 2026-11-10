"""The MCP server's built-in routers by name: the config variable, the keys it reads, and its notes and messages.

Experimental. Kept apart from :mod:`web_router.constants_mcp`, so that file stops growing.
"""

from __future__ import annotations

from typing import Final

ENV_MCP_ROUTER_CONFIG: Final = "WEB_ROUTER_MCP_ROUTER_CONFIG"
"""The ``router_config`` of the built-in router ``WEB_ROUTER_MCP_ROUTER`` names, as a JSON object.

Read once, when the server starts, from the process environment only: no ``.env`` file and no ``load_env``
call can set or change it. Set, it is the whole config: ``remote`` then reads its URL from it, not from
``WEB_ROUTER_REMOTE_ROUTER_URL``.
"""

MCP_CONFIG_KEY_ENV: Final = "api_key_env"
"""A config key, at any depth, naming the variable that holds a key: read at startup and put in its place."""

MCP_CONFIG_KEY: Final = "api_key"
"""What :data:`MCP_CONFIG_KEY_ENV` becomes: the key's value, ``""`` when the variable is unset."""

MCP_CONFIG_URL_KEYS: Final = ("url", "base_url")
"""The config keys, at any depth, holding a service's URL: the capabilities resource names each one's host."""

MCP_ROUTER_BUILT_IN = "built_in"
MCP_ROUTER_BUILT_IN_NOTE = (
    "A built-in router the operator configured, running in this process; its config names no service URL, "
    "but a router that calls a model may send the prompt to that model's server."
)
"""Where a built-in router whose config names no URL runs, and what that means for the prompt."""

MCP_ROUTER_HOSTS_PROMPT_NOTE = (
    " With this router, web_run also sends each prompt to {hosts}, the URLs in it whole, query strings included, "
    "before the plan is made: to each router it asks, one that times out included."
)
"""What the ``prompt_note`` adds when the router's config names service URLs."""

MCP_MSG_ROUTER_CONFIG = "{env_var} must be a JSON object of router_config entries; {why}."
MCP_WHY_NOT_JSON = "it is not valid JSON"
MCP_WHY_NOT_OBJECT = "it is JSON, but not an object"
MCP_MSG_KEY_ENV = "{env_var}: {key} must name an environment variable; got a {kind}."
MCP_MSG_CONFIG_UNNAMED = "{env_var} configures a built-in router; {router_var} names {spec!r}, which is not one."
MCP_MSG_KEY_HOME = "{env_var}: {key_var} is sent only to {home}; router {name!r} here posts elsewhere."
MCP_MSG_KEY_UNSET = (
    "{env_var}: router {name!r} needs {key_var} set in the server's own process environment at startup; "
    "a .env file or load_env never supplies a router's key."
)

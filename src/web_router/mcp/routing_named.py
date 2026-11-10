"""A built-in router the operator names for ``web_run``: its config read from the environment, its name and hosts.

Experimental. ``WEB_ROUTER_MCP_ROUTER`` may name any built-in router, and
``WEB_ROUTER_MCP_ROUTER_CONFIG`` holds its ``router_config`` as a JSON object,
both read once at startup from the process environment, before any ``.env``
file is loaded. Each key a router would read -- ``api_key_env``'s variable, or
a preset's -- is read then and put in its config as ``api_key``
(:mod:`.routing_keys`), so the router never reads the environment again. The capabilities resource names
the router and each service it would send the prompt to, as scheme and host.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from web_router._urls import host_of
from web_router.constants_fallback import (
    FALLBACK_CUSTOM_NAME,
    FALLBACK_DISPLAY_NAME,
    FALLBACK_MEMBERS_KEY,
    FALLBACK_NAME_KEY,
    FALLBACK_ROUTER_NAME,
)
from web_router.constants_mcp_router import (
    ENV_MCP_ROUTER_CONFIG,
    MCP_MSG_ROUTER_CONFIG,
    MCP_WHY_NOT_JSON,
    MCP_WHY_NOT_OBJECT,
)
from web_router.exceptions import ConfigurationError
from web_router.mcp.routing_keys import DECIDER_PRESETS, captured_key, hostname, target_url


def read_config(env: Mapping[str, str], name: str | None) -> dict[str, Any] | None:
    """The config ``WEB_ROUTER_MCP_ROUTER_CONFIG`` sets for router ``name``, keys captured from ``env``.

    ``None`` when it is unset.

    Raises:
        ConfigurationError: for a value that is not a JSON object, or a key :func:`.routing_keys.captured_key`
            refuses. The message never quotes the value, which may hold a key.
    """
    raw = env.get(ENV_MCP_ROUTER_CONFIG)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
        why = MCP_WHY_NOT_JSON
    else:
        why = MCP_WHY_NOT_OBJECT
    if not isinstance(parsed, dict):
        raise ConfigurationError(MCP_MSG_ROUTER_CONFIG.format(env_var=ENV_MCP_ROUTER_CONFIG, why=why))
    captured: dict[str, Any] = _captured(parsed, env, name)
    return captured


def _own_name(value: dict[str, Any], name: str | None) -> str | None:
    """The router a mapping configures: the ``name`` it holds, as a ``fallback`` member does, else ``name``.

    Stripped, as the registry strips a name it resolves, so ``"llm "`` finds the ``llm`` presets.
    """
    own = value.get(FALLBACK_NAME_KEY)
    return own.strip() if isinstance(own, str) else name


def _captured(value: Any, env: Mapping[str, str], name: str | None) -> Any:
    """``value`` with each router's key read now from ``env`` and put in it as ``api_key``."""
    if isinstance(value, list):
        return [_captured(item, env, None) for item in value]
    if not isinstance(value, dict):
        return value
    out = {key: _captured(item, env, None) for key, item in value.items()}
    return captured_key(_own_name(value, name), out, env)


def hosts_in(config: object, name: str | None = None) -> list[str]:
    """Each service ``config`` posts to, at any depth, a preset's included, as scheme and host, once each."""
    found: list[str] = []
    if isinstance(config, list):
        for item in config:
            found.extend(hosts_in(item))
    elif isinstance(config, dict):
        if (url := target_url(_own_name(config, name), config)) is not None:
            found.append(host_of(url))
        for item in config.values():
            found.extend(hosts_in(item))
    return list(dict.fromkeys(found))


def display_name(name: str, config: Mapping[str, Any] | None) -> str:
    """What the capabilities resource calls a built-in router, as the decision record names it once built.

    ``fallback(<member>, ...)``; ``llm(<host>)`` and ``systemone(<host>)``, the host alone; else ``name(<scheme>://<host>)``.
    """
    config = config or {}
    members = config.get(FALLBACK_MEMBERS_KEY)
    if name == FALLBACK_ROUTER_NAME and isinstance(members, list):
        return FALLBACK_DISPLAY_NAME.format(members=", ".join(_member_name(member) for member in members))
    url = target_url(name, config)
    if url is None:
        return name
    return f"{name}({hostname(url) if name in DECIDER_PRESETS else host_of(url)})"


def _member_name(member: object) -> str:
    if isinstance(member, str):
        return display_name(member.strip(), None)
    if isinstance(member, dict) and isinstance(member.get(FALLBACK_NAME_KEY), str):
        return display_name(member[FALLBACK_NAME_KEY].strip(), member)
    return FALLBACK_CUSTOM_NAME

"""A built-in router the operator names for ``web_run``: its config read from the environment, its name and hosts.

Experimental. ``WEB_ROUTER_MCP_ROUTER`` may name any built-in router, and
``WEB_ROUTER_MCP_ROUTER_CONFIG`` holds its ``router_config`` as a JSON object,
both read once at startup from the process environment. A config entry
``api_key_env`` names a variable whose value is read then and put in its place
as ``api_key``, so the router never reads the environment again: a later
``load_env`` or change to it moves nothing. The capabilities resource names
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
    MCP_CONFIG_KEY,
    MCP_CONFIG_KEY_ENV,
    MCP_CONFIG_URL_KEYS,
    MCP_MSG_KEY_ENV,
    MCP_MSG_ROUTER_CONFIG,
    MCP_WHY_NOT_JSON,
    MCP_WHY_NOT_OBJECT,
)
from web_router.exceptions import ConfigurationError


def read_config(env: Mapping[str, str]) -> dict[str, Any] | None:
    """The router config ``WEB_ROUTER_MCP_ROUTER_CONFIG`` sets, keys captured from ``env``; ``None`` when unset.

    Raises:
        ConfigurationError: for a value that is not a JSON object, or an ``api_key_env`` that is not a string.
            The message never quotes the value, which may hold a key.
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
    captured: dict[str, Any] = _captured(parsed, env)
    return captured


def _captured(value: Any, env: Mapping[str, str]) -> Any:
    """``value`` with every ``api_key_env`` replaced by ``api_key``, the variable's value read now."""
    if isinstance(value, list):
        return [_captured(item, env) for item in value]
    if not isinstance(value, dict):
        return value
    out = {key: _captured(item, env) for key, item in value.items() if key != MCP_CONFIG_KEY_ENV}
    if MCP_CONFIG_KEY_ENV in value:
        variable = value[MCP_CONFIG_KEY_ENV]
        if not isinstance(variable, str):
            kind = type(variable).__name__
            message = MCP_MSG_KEY_ENV.format(env_var=ENV_MCP_ROUTER_CONFIG, key=MCP_CONFIG_KEY_ENV, kind=kind)
            raise ConfigurationError(message)
        out[MCP_CONFIG_KEY] = env.get(variable) or ""
    return out


def hosts_in(config: object) -> list[str]:
    """Each service URL ``config`` names, at any depth, as scheme and host, once each, in order."""
    found: list[str] = []
    if isinstance(config, list):
        for item in config:
            found.extend(hosts_in(item))
    elif isinstance(config, dict):
        for key, item in config.items():
            found.extend([host_of(item)] if key in MCP_CONFIG_URL_KEYS and isinstance(item, str) else hosts_in(item))
    return list(dict.fromkeys(found))


def display_name(name: str, config: Mapping[str, Any] | None) -> str:
    """What the capabilities resource calls a built-in router: ``fallback(<member>, ...)``, or ``name(<host>)``."""
    members = (config or {}).get(FALLBACK_MEMBERS_KEY)
    if name == FALLBACK_ROUTER_NAME and isinstance(members, list):
        return FALLBACK_DISPLAY_NAME.format(members=", ".join(_member_name(member) for member in members))
    hosts = hosts_in({key: value for key, value in (config or {}).items() if key in MCP_CONFIG_URL_KEYS})
    return f"{name}({hosts[0]})" if hosts else name


def _member_name(member: object) -> str:
    if isinstance(member, str):
        return display_name(member, None)
    if isinstance(member, dict) and isinstance(member.get(FALLBACK_NAME_KEY), str):
        return display_name(member[FALLBACK_NAME_KEY], member)
    return FALLBACK_CUSTOM_NAME

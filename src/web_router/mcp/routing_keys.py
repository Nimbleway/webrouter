"""Where a built-in router named for ``web_run`` posts, and its key, captured from the process environment.

Experimental. See :mod:`web_router.mcp.routing_named`. A router's key comes only
from the operator's process environment at startup, never from a ``.env``
file or ``load_env``, which fill provider credentials -- and a preset's
variable, such as ``DEEPSEEK_API_KEY``, is a provider credential too. So each
key a router would read is read here, before any file is loaded, and put in
its config as ``api_key``: the variable ``api_key_env`` names, or for an
``llm`` or ``systemone`` preset given no key, the preset's own variable. A
vendor's variable is captured only for a router posting to that vendor's host.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from web_router.constants_decider import (
    KEY_HOMES,
    LLM_PRESETS,
    LLM_ROUTER_NAME,
    SYSTEMONE_PRESETS,
    SYSTEMONE_ROUTER_NAME,
)
from web_router.constants_mcp_router import (
    ENV_MCP_ROUTER_CONFIG,
    MCP_CONFIG_KEY,
    MCP_CONFIG_KEY_ENV,
    MCP_CONFIG_URL_KEYS,
    MCP_MSG_KEY_ENV,
    MCP_MSG_KEY_HOME,
    MCP_MSG_KEY_UNSET,
)
from web_router.exceptions import ConfigurationError

DECIDER_PRESETS: dict[str, dict[str, tuple[str, str]]] = {
    LLM_ROUTER_NAME: {preset: (row[0], row[1]) for preset, row in LLM_PRESETS.items()},
    SYSTEMONE_ROUTER_NAME: {preset: (row[0], row[3]) for preset, row in SYSTEMONE_PRESETS.items()},
}
"""Each decider's presets as (base URL, key variable), read from the deciders' own tables."""


def _preset(name: str | None, config: Mapping[str, Any]) -> tuple[str, str] | None:
    """The (base URL, key variable) of the preset ``config`` names for router ``name``; ``None`` for none."""
    preset = config.get("preset")
    return DECIDER_PRESETS.get(name or "", {}).get(preset) if isinstance(preset, str) else None


def target_url(name: str | None, config: Mapping[str, Any]) -> str | None:
    """The URL router ``name`` posts to: its preset's base URL, else the ``url`` or ``base_url`` it is given."""
    if (row := _preset(name, config)) is not None:
        return row[0]
    return next((value for key in MCP_CONFIG_URL_KEYS if isinstance(value := config.get(key), str)), None)


def hostname(url: str) -> str:
    """A URL's host alone, as a decider names itself by it."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _variable(name: str | None, config: Mapping[str, Any]) -> str | None:
    """The variable router ``name`` would read its key from: ``api_key_env``, else its preset's, unless given one."""
    if MCP_CONFIG_KEY_ENV in config:
        variable = config[MCP_CONFIG_KEY_ENV]
        if not isinstance(variable, str):
            kind = type(variable).__name__
            raise ConfigurationError(
                MCP_MSG_KEY_ENV.format(env_var=ENV_MCP_ROUTER_CONFIG, key=MCP_CONFIG_KEY_ENV, kind=kind)
            )
        return variable
    row = _preset(name, config)
    return None if row is None or MCP_CONFIG_KEY in config else row[1]


def captured_key(name: str | None, config: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    """``config`` with the key it would read put in it as ``api_key``, from ``env``, and ``api_key_env`` dropped.

    Raises:
        ConfigurationError: for an ``api_key_env`` that is not a string, a vendor's variable for a router posting
            elsewhere, or an ``llm`` or ``systemone`` router whose variable is unset. ``remote`` with its variable
            unset sends no key.
    """
    variable = _variable(name, config)
    if variable is None:
        return config
    home = KEY_HOMES.get(variable)
    target = target_url(name, config)
    if home is not None and (target is None or hostname(target) != home):
        raise ConfigurationError(
            MCP_MSG_KEY_HOME.format(env_var=ENV_MCP_ROUTER_CONFIG, key_var=variable, home=home, name=name)
        )
    value = env.get(variable) or ""
    if not value and name in DECIDER_PRESETS:
        raise ConfigurationError(MCP_MSG_KEY_UNSET.format(env_var=ENV_MCP_ROUTER_CONFIG, key_var=variable, name=name))
    out = {key: item for key, item in config.items() if key != MCP_CONFIG_KEY_ENV}
    out[MCP_CONFIG_KEY] = value
    return out

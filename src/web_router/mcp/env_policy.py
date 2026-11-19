"""The operator's switches over where keys may come from: ``load_env``, a remembered file, the working directory.

Each is read from the real environment only -- none is a credential, so no
``.env`` can set one -- and each defaults to what the server did before it
existed (:data:`~web_router.constants_mcp_env.MCP_ENV_SWITCHES`). A value
that is not one of a switch's own stops the server rather than being guessed at.
"""

from __future__ import annotations

import os

from web_router.constants_mcp_env import MCP_ENV_SWITCHES, MCP_MSG_ENV_BAD_SWITCH
from web_router.exceptions import ConfigurationError


def switch(name: str) -> str:
    """The value of the switch ``name``: its default when unset or blank, lower-cased otherwise.

    Raises:
        ConfigurationError: for a value the switch does not take.
    """
    values, default = MCP_ENV_SWITCHES[name]
    value = os.environ.get(name, "").strip().lower() or default
    if value not in values:
        raise ConfigurationError(
            MCP_MSG_ENV_BAD_SWITCH.format(name=name, value=value, values=", ".join(values), default=default)
        )
    return value

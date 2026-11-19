"""What the model is told about adding keys, matched to whether this server serves ``load_env``.

With ``load_env`` off -- the operator's choice, and the default over HTTP -- the tool is not listed, so the
instructions and hints point at the server's environment instead of telling the model to call it.
"""

from __future__ import annotations

from pathlib import Path

from web_router.constants import (
    MCP_MSG_ENV_HINT,
    MCP_MSG_INSTRUCTIONS_NO_KEYS,
    MCP_MSG_NO_BACKEND_HINT,
)
from web_router.constants_mcp import MCP_MSG_KEY_HINT
from web_router.constants_mcp_env import ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_OFF
from web_router.constants_mcp_instructions import (
    MCP_MSG_ENV_HINT_OPERATOR,
    MCP_MSG_INSTRUCTIONS_NO_KEYS_OPERATOR,
    MCP_MSG_KEY_HINT_OPERATOR,
    MCP_MSG_NO_BACKEND_HINT_OPERATOR,
    instructions_text,
)
from web_router.mcp.env_policy import switch


def load_env_served() -> bool:
    """Whether this server lists and serves ``load_env``."""
    return switch(ENV_MCP_LOAD_ENV) != MCP_LOAD_ENV_OFF


def server_instructions(*, keyed: bool, remote: bool = False) -> str:
    """The server's instructions; ``remote`` for a server reached over HTTP, whose browsers open on it."""
    served = load_env_served()
    text = instructions_text(load_env=served, remote=remote)
    if not keyed:
        text += MCP_MSG_INSTRUCTIONS_NO_KEYS if served else MCP_MSG_INSTRUCTIONS_NO_KEYS_OPERATOR
    return text


def key_hint() -> str:
    """Said after a library error that named ``provider_config``: how a key reaches this server."""
    return MCP_MSG_KEY_HINT if load_env_served() else MCP_MSG_KEY_HINT_OPERATOR


def no_backend_hint(env_vars: list[str]) -> str:
    """Added to a tool's description when nothing serves it: the variables that would."""
    hint = MCP_MSG_NO_BACKEND_HINT if load_env_served() else MCP_MSG_NO_BACKEND_HINT_OPERATOR
    return hint.format(env_vars=", ".join(env_vars))


def env_hint(env_var: str, user_dir: Path) -> str:
    """The capabilities resource's hint when no key reached the server."""
    if not load_env_served():
        return MCP_MSG_ENV_HINT_OPERATOR
    return MCP_MSG_ENV_HINT.format(env_var=env_var, user_dir=user_dir)

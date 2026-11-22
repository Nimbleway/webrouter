"""The MCP server's ``.env`` constants: ``load_env``'s schema and messages, the operator's key-source switches,
and the startup warnings. Apart from ``constants.py`` so that file stops growing."""

from __future__ import annotations

from typing import Any

MCP_MSG_ENV_FOREIGN = (
    "Keys from {paths} are in use: a .env that an earlier load_env(remember=true) recorded, outside the "
    "directory this server started in. Calls through those keys bill those accounts. If this setup should be "
    "keyless, delete the record at {pointer}."
)
"""The ``warning`` that leads the capabilities resource's ``env`` when a remembered file from elsewhere was loaded."""

MCP_MSG_INSTRUCTIONS_FOREIGN_ENV = (
    " Keys from a remembered .env outside this project ({paths}) were loaded at startup, so billed backends "
    "are live here: before relying on a keyless setup, read web-router://capabilities and tell the user."
)
"""Added to the server instructions in the same case, since a model may never read the resource."""

MCP_ARG_PATH = "path"
MCP_ARG_REMEMBER = "remember"
MCP_ARG_FORGET = "forget"

MCP_LOAD_ENV_PROPERTIES: dict[str, dict[str, Any]] = {
    MCP_ARG_PATH: {
        "type": "string",
        "description": "The .env file's path as the user gave it, e.g. '~/work/.env'.",
    },
    MCP_ARG_REMEMBER: {
        "type": "boolean",
        "description": "Read it on every start too; only when the user asks.",
    },
    MCP_ARG_FORGET: {
        "type": "boolean",
        "description": "Forget the remembered file; no `path`.",
    },
}

MCP_DESC_LOAD_ENV = (
    "Load API keys from a .env file the user names. Ask where it is; "
    "never ask for a key. Only provider credentials are read, an already-set variable wins, and only "
    "names are returned. Set `remember` (read it on every start) only when the user asks."
)

MCP_MSG_ENV_BAD_ARGUMENTS = (
    "load_env takes either `path` (a string, with `remember` as a boolean) or `forget: true` on its own."
)

ENV_MCP_LOAD_ENV = "WEB_ROUTER_MCP_LOAD_ENV"
"""What the ``load_env`` tool may do: load and remember, load for this session only, or nothing (not listed)."""

MCP_LOAD_ENV_REMEMBER = "remember"
MCP_LOAD_ENV_LOAD = "load"
MCP_LOAD_ENV_OFF = "off"
MCP_LOAD_ENV_DEFAULT = MCP_LOAD_ENV_REMEMBER

ENV_MCP_REMEMBERED_ENV = "WEB_ROUTER_MCP_REMEMBERED_ENV"
"""Where a remembered ``.env`` is read at startup: in any directory, only inside the one it lies in, or never."""

MCP_REMEMBERED_ANYWHERE = "anywhere"
MCP_REMEMBERED_PROJECT = "project"
MCP_REMEMBERED_OFF = "off"
MCP_REMEMBERED_DEFAULT = MCP_REMEMBERED_ANYWHERE

ENV_MCP_CWD_ENV = "WEB_ROUTER_MCP_CWD_ENV"
"""Whether the ``.env`` in the directory the server starts in is read at startup."""

MCP_CWD_ENV_ON = "on"
MCP_CWD_ENV_OFF = "off"
MCP_CWD_ENV_DEFAULT = MCP_CWD_ENV_ON

ENV_MCP_PRIVATE_URLS = "WEB_ROUTER_MCP_PRIVATE_URLS"
"""Whether the browser tools may open loopback and private-network addresses: on by default, off over HTTP,
where the browser runs on a shared server and those addresses are the server's own network."""

MCP_PRIVATE_URLS_ON = "on"
MCP_PRIVATE_URLS_OFF = "off"
MCP_PRIVATE_URLS_DEFAULT = MCP_PRIVATE_URLS_ON

MCP_MSG_BROWSER_REFUSED_PRIVATE = (
    "that is a loopback or private-network address, which this server may not open ({env_var}=off)"
)

MCP_ENV_SWITCHES: dict[str, tuple[tuple[str, ...], str]] = {
    ENV_MCP_LOAD_ENV: ((MCP_LOAD_ENV_REMEMBER, MCP_LOAD_ENV_LOAD, MCP_LOAD_ENV_OFF), MCP_LOAD_ENV_DEFAULT),
    ENV_MCP_REMEMBERED_ENV: (
        (MCP_REMEMBERED_ANYWHERE, MCP_REMEMBERED_PROJECT, MCP_REMEMBERED_OFF),
        MCP_REMEMBERED_DEFAULT,
    ),
    ENV_MCP_CWD_ENV: ((MCP_CWD_ENV_ON, MCP_CWD_ENV_OFF), MCP_CWD_ENV_DEFAULT),
    ENV_MCP_PRIVATE_URLS: ((MCP_PRIVATE_URLS_ON, MCP_PRIVATE_URLS_OFF), MCP_PRIVATE_URLS_DEFAULT),
}
"""Each switch's accepted values and its default, which is today's behaviour. Unset or blank is the default."""

MCP_MSG_ENV_BAD_SWITCH = "{name}={value!r} is not one of {values}; unset it for the default, {default}."

MCP_ENV_STATUS_SKIPPED = "skipped"
MCP_ENV_STATUS_REFUSED = "refused"
MCP_ENV_REASON_SWITCHED_OFF = "{name}={value}"
MCP_ENV_REASON_OUTSIDE_PROJECT = "it is outside the working directory, and {name}={value}"

MCP_MSG_ENV_NOT_A_DOTENV_NAME = (
    "load_env opens only a file named .env, .env.<name> or <name>.env, so {path} was not opened and "
    "nothing was loaded. Ask the user for their .env file's path."
)

MCP_MSG_ENV_REMEMBER_OFF = (
    "This server's operator does not let load_env remember a file ({name}={value}), so nothing was loaded. "
    "Call load_env again without `remember` to load it for this session only."
)

MCP_MSG_ENV_FORGOTTEN = (
    "Forgot {path}: the server will not read it on its next start. Keys it already loaded stay set "
    "until the server restarts."
)
MCP_MSG_ENV_NOTHING_TO_FORGET = "No remembered file was recorded, so there was nothing to forget."
MCP_MSG_ENV_FORGET_FAILED = "{pointer} could not be removed ({reason}); delete it by hand to forget the file."

MCP_MSG_ENV_LOOSE_PERMISSIONS = (
    "{path} can be read by other users of this machine; `chmod 600 {path}` keeps its keys to you."
)

MCP_LOG_ENV_REMEMBERED = (
    "web-router: keys from %s are in use, a .env that an earlier load_env(remember=true) recorded. Calls "
    "through them bill those accounts. To stop reading it, delete %s or call load_env with forget=true."
)
MCP_LOG_ENV_EXPLICIT_MISSING = (
    "web-router: %s names no readable file (%s); the server went on to read the other places it looks."
)
MCP_LOG_ENV_LOOSE_PERMISSIONS = "web-router: %s"

MCP_LOG_NO_CEILING = (
    "web-router: billed keys are configured and %s is unset, so nothing caps what one connection spends."
)

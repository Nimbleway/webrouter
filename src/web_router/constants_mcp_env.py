"""The MCP server's warning about a remembered ``.env`` from another directory, apart so large files stop growing."""

from __future__ import annotations

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

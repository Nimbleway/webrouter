"""A failed tool call's message, as the model reads it."""

from __future__ import annotations

import re

from web_router.constants_mcp import MCP_MSG_KEY_HINT, MCP_PROVIDER_CONFIG_CLAUSE
from web_router.mcp.shaping import redact, redaction_note


def error_text(exc: Exception) -> str:
    """``exc``'s message, rewritten for MCP and redacted.

    Every tool's error is redacted, here rather than in each handler, so no
    failure path can miss it: a browse backend's own error can quote the
    session URL, and a vendor's HTTP error body rides on any method's message.
    An ordinary message passes through untouched.
    """
    message, count = redact(_for_mcp(str(exc)))
    return str(message) + redaction_note(count)


def _for_mcp(message: str) -> str:
    """A library message as an MCP user can act on it: ``provider_config`` is code, so the ``.env`` route instead."""
    rewritten, count = re.subn(MCP_PROVIDER_CONFIG_CLAUSE, "", message)
    return rewritten + MCP_MSG_KEY_HINT if count else message

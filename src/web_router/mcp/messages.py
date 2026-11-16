"""A failed tool call's message, as the model reads it."""

from __future__ import annotations

import re

from web_router.constants_mcp import MCP_MSG_KEY_HINT, MCP_PROVIDER_CONFIG_ASIDE, MCP_PROVIDER_CONFIG_CLAUSE
from web_router.constants_mcp_context import MCP_MSG_ERROR_FACTS
from web_router.mcp.shaping import redact, redaction_note
from web_router.types_errors import is_retryable


def error_text(exc: Exception) -> str:
    """``exc``'s message, rewritten for MCP and redacted.

    Every tool's error is redacted, here rather than in each handler, so no
    failure path can miss it: a browse backend's own error can quote the
    session URL, and a vendor's HTTP error body rides on any method's message.
    An ordinary message passes through untouched. A backend's failure ends in
    its ``kind`` and ``retryable``, which the text alone would leave a model to guess.
    """
    message, count = redact(_for_mcp(str(exc)))
    return str(message) + redaction_note(count) + _facts(exc)


def _facts(exc: Exception) -> str:
    """`` (kind=..., retryable=...)`` for an error that has a kind, else nothing."""
    kind = getattr(exc, "kind", None)
    if not isinstance(kind, str):
        return ""
    retryable = getattr(exc, "retryable", None)
    retryable = is_retryable(kind) if not isinstance(retryable, bool) else retryable
    return MCP_MSG_ERROR_FACTS.format(kind=kind, retryable=str(retryable).lower())


def _for_mcp(message: str) -> str:
    """A library message as an MCP user can act on it: ``provider_config`` is code, so the ``.env`` route instead.

    An aside beside a setting that is not a key just goes: ``load_env`` would not set it.
    """
    message = re.sub(MCP_PROVIDER_CONFIG_ASIDE, "", message)
    rewritten, count = re.subn(MCP_PROVIDER_CONFIG_CLAUSE, "", message)
    return rewritten + MCP_MSG_KEY_HINT if count else message

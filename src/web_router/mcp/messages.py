"""A failed tool call's message, as the model reads it."""

from __future__ import annotations

import logging
import re
import traceback

from mcp_types import CallToolResult, TextContent

from web_router.constants_mcp import MCP_PROVIDER_CONFIG_ASIDE, MCP_PROVIDER_CONFIG_CLAUSE
from web_router.constants_mcp_context import MCP_LOG_TOOL_RAISED, MCP_MSG_ERROR_FACTS
from web_router.exceptions import WebRouterError
from web_router.mcp.key_text import key_hint
from web_router.mcp.masking import mask_configured_text
from web_router.mcp.shaping import redact, redaction_note
from web_router.types_errors import is_retryable

_LOG = logging.getLogger(__name__)


def failure_result(exc: Exception) -> CallToolResult:
    """A failed call as the tool error the model reads; a bug is logged too, with its stack but not its raw text.

    ``WebRouterError`` and ``ValueError`` are the library's failures and bad arguments. Anything else is a bug,
    so the operator gets its traceback, frames only: the exception's own text can quote a key, so it is masked.
    """
    text = error_text(exc)
    if not isinstance(exc, WebRouterError | ValueError):
        frames = "".join(traceback.format_tb(exc.__traceback__))
        _LOG.error(MCP_LOG_TOOL_RAISED, type(exc).__name__, text, frames)
    return CallToolResult(content=[TextContent(text=text)], is_error=True)


def error_text(exc: Exception) -> str:
    """``exc``'s message, rewritten for MCP and redacted.

    Every tool's error is redacted, here rather than in each handler, so no
    failure path can miss it: a browse backend's own error can quote the
    session URL, and a vendor's HTTP error body rides on any method's message.
    Every configured credential's value is masked too, wherever it sits, so
    a backend that echoes a key in plain text does not hand it on.
    An ordinary message passes through untouched. A backend's failure ends in
    its ``kind`` and ``retryable``, which the text alone would leave a model to guess.
    """
    message, count = redact(_for_mcp(str(exc)))
    # Masked after redaction: a short key masked first could break a URL the redactor must still recognise.
    return mask_configured_text(str(message)) + redaction_note(count) + _facts(exc)


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
    return rewritten + key_hint() if count else message

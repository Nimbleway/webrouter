"""The ``fetch_raw`` tool: a result's whole payload, by the handle the shaped result carried.

Kept out of :mod:`web_router.mcp.tools`, which is at its size limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from web_router.constants import MCP_ARG_MAX_CHARS, MCP_BROWSER_TOOLS, MCP_TOOL_FETCH_RAW
from web_router.constants_mcp_context import (
    MCP_ARG_OFFSET,
    MCP_MSG_NO_RAW,
    MCP_MSG_RAW_BAD_PAGE,
    MCP_MSG_RAW_NEXT_PAGE,
    MCP_MSG_RAW_PAGE,
    MCP_MSG_RAW_PAST_END,
)
from web_router.mcp.masking import configured_secrets, mask_configured
from web_router.mcp.shaping import Record, max_response_chars, redact, redaction_note
from web_router.mcp.shaping_fit import render
from web_router.providers.base import BrowseJob

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools

# What one character costs once it is a JSON string: a quote or a backslash is escaped, a control character too
# (\n, \t, ... or \u00XX). Every other character, non-ASCII included, is sent as it is (`ensure_ascii=False`).
_ESCAPED = {'"': 2, "\\": 2, "\b": 2, "\f": 2, "\n": 2, "\r": 2, "\t": 2}
_CONTROL_ESCAPED = 6


@dataclass(frozen=True)
class _Pages:
    """A payload's masked JSON, for the raw object and the credentials it was masked with."""

    raw: Any
    secrets: int
    text: str
    note: str | None


async def fetch_raw_tool(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The backend's payload with every configured credential's value masked; a browse or browser one redacted too.

    With ``offset`` or ``max_chars``, one page of the payload's JSON instead of the whole of it.
    """
    record = tools.record(MCP_TOOL_FETCH_RAW, arguments)
    if record.raw is None:
        raise ValueError(MCP_MSG_NO_RAW.format(handle=record.handle))
    offset = _page_argument(arguments, MCP_ARG_OFFSET, minimum=0)
    max_chars = _page_argument(arguments, MCP_ARG_MAX_CHARS, minimum=1)
    if offset is None and max_chars is None:
        # Deliberately unbounded unless a page is asked for: this tool exists to hand back exactly what
        # the shaped result could not carry.
        raw, note = _masked(record)
        return {"handle": record.handle, "tool": record.tool, "raw": raw, **({"note": note} if note else {})}
    return _page(record, offset or 0, max_chars or max_response_chars())


def _masked(record: Record) -> tuple[Any, str | None]:
    """``record``'s payload as ``fetch_raw`` may show it, and the redaction note if anything was redacted."""
    if not isinstance(record.job, BrowseJob) and record.tool not in MCP_BROWSER_TOOLS:
        return mask_configured(record.raw), None
    # A browse payload is the one that is not handed back verbatim: it can
    # carry the session the agent ran in, and a live view or a key-bearing
    # CDP URL is control of a billed browser, not data about the task.
    raw, count = redact(record.raw)
    raw = mask_configured(raw)  # after redaction, so a masked short key cannot hide a URL from it
    return raw, redaction_note(count).strip() if count else None


def _page_argument(arguments: Mapping[str, Any], name: str, *, minimum: int) -> int | None:
    value: object = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(MCP_MSG_RAW_BAD_PAGE.format(name=name, minimum=minimum, value=value))
    return value


def _pages(record: Record) -> _Pages:
    """``record``'s masked JSON, built once for all its pages.

    Built again only when the payload is a new object (a research run that finished since) or a credential was
    added or changed (``load_env``), so the mask it carries is always the current one.
    """
    secrets = hash(tuple(configured_secrets()))
    cached = record.pages
    if isinstance(cached, _Pages) and cached.raw is record.raw and cached.secrets == secrets:
        return cached
    raw, note = _masked(record)
    pages = _Pages(raw=record.raw, secrets=secrets, text=render(raw), note=note)
    record.pages = pages
    return pages


def _page_end(text: str, offset: int, max_chars: int) -> int:
    """Where a page from ``offset`` ends so that, sent as a JSON string, it takes at most ``max_chars`` characters.

    The page is a slice of JSON sent inside JSON, so its quotes, backslashes and line breaks are escaped a second time
    on the way out: 8,000 characters of a page of line breaks would arrive as 16,000, past the cut the page exists to
    get under. A page always holds at least one character, so reading the next one always moves forward.
    """
    end, size = offset, 0
    while end < len(text):
        char = text[end]
        cost = _ESCAPED.get(char, _CONTROL_ESCAPED if char < " " else 1)
        if size + cost > max_chars and end > offset:
            break
        size += cost
        end += 1
    return end


def _page(record: Record, offset: int, max_chars: int) -> dict[str, Any]:
    """One page of the payload's JSON, from ``offset``, and where the next one starts.

    A client that shortens a long tool result keeps its start and end and drops the middle: Codex as shipped keeps
    about 10 KiB, while a finished research run's payload is two to three times its report, 40,000 characters and
    more. The whole payload in one result reached such a model with its middle gone; pages of it reach it whole, a
    page per call, and a model stops once it has what it needs. Masking and redaction ran on the whole payload
    first, so a page boundary cannot split a credential or a session URL out of their reach.
    """
    pages = _pages(record)
    total = len(pages.text)
    if offset >= total:
        raise ValueError(MCP_MSG_RAW_PAST_END.format(offset=offset, handle=record.handle, total=total))
    end = _page_end(pages.text, offset, max_chars)
    note = MCP_MSG_RAW_PAGE.format(start=offset, end=end, total=total)
    if end < total:
        note += MCP_MSG_RAW_NEXT_PAGE.format(handle=record.handle, next_offset=end)
    if pages.note:
        note = f"{pages.note} {note}"
    paged: dict[str, Any] = {
        "handle": record.handle,
        "tool": record.tool,
        "page": pages.text[offset:end],
        "offset": offset,
    }
    if end < total:
        paged["next_offset"] = end
    return {**paged, "total_chars": total, "note": note}

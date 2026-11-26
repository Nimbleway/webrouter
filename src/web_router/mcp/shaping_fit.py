"""Mark web text and fit shaped results to the size budget measured on their JSON."""

from __future__ import annotations

import json
from typing import Any

from web_router.constants import (
    MCP_MIN_FIELD_CHARS,
    MCP_MSG_DROPPED,
    MCP_MSG_TRUNCATED,
    MCP_TRUNCATION_MARKER,
    MCP_WORD_SEARCH_WINDOW,
)
from web_router.constants_mcp_context import MCP_JSON_SEPARATORS
from web_router.constants_mcp_instructions import MCP_UNTRUSTED_MARK


def render(payload: Any) -> str:
    """Serialize a shaped result the way the tool returns it.

    The budget is measured with it too, so the cap counts the characters that reach the client.
    """
    return json.dumps(payload, separators=MCP_JSON_SEPARATORS, ensure_ascii=False, default=str)


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters at a word boundary, marking where it was cut."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Only a boundary near the cut will do: honouring one further back would
    # throw away text that fits, which is a worse trade than a split word.
    boundary = head.rfind(" ", limit - MCP_WORD_SEARCH_WINDOW)
    if boundary > 0:
        head = head[:boundary]
    return head.rstrip() + MCP_TRUNCATION_MARKER


def _mark_untrusted(payload: dict[str, Any]) -> None:
    # Keep the same dict: live data's later size pass holds references into it.
    original = payload.copy()
    payload.clear()
    payload.update(MCP_UNTRUSTED_MARK)
    payload.update(original)


def _fit(
    payload: dict[str, Any],
    items: list[dict[str, Any]],
    text_keys: tuple[str, ...],
    *,
    handle: str,
    budget: int,
    shortened: bool = False,
    extra_note: str = "",
    main: bool = True,
    untrusted: bool = True,
) -> dict[str, Any]:
    """Shrink a shaped payload until it serializes inside ``budget``, and say so if it did.

    ``main`` makes cutting item text set ``truncated``; otherwise only a cut answer or dropped item does.
    Catalogue shapers set ``untrusted=False`` because their text describes sources rather than pages.
    Text shrinks to ``MCP_MIN_FIELD_CHARS`` before whole items are dropped from the end of the ranking.
    The last item is squeezed further: an empty list tells the reader less than a heavily cut result.
    """
    share = max(MCP_MIN_FIELD_CHARS, budget // max(len(items), 1))
    dropped, total = 0, len(items)
    if untrusted:
        _mark_untrusted(payload)
    while True:
        shortened = (any([_shorten(item, text_keys, share) for item in items]) and main) or shortened  # cut every item
        payload["handle"] = handle
        payload["truncated"] = shortened or dropped > 0
        _set_note(payload, handle, dropped, total, extra_note)
        overflow = len(render(payload)) - budget
        if overflow <= 0:
            return payload
        if len(items) > 1 and share > MCP_MIN_FIELD_CHARS:
            # Spread the overflow over the items; the marker each cut adds back counts too.
            share = max(MCP_MIN_FIELD_CHARS, share - -(-overflow // len(items)) - len(MCP_TRUNCATION_MARKER))
        elif len(items) > 1:
            items.pop()
            dropped += 1
        # The marker is added back by the cut, so its own length has to come out
        # of the request or a shrink that small makes the text no shorter.
        elif not any(
            _shorten(item, text_keys, _room(item, text_keys) - overflow - len(MCP_TRUNCATION_MARKER)) for item in items
        ):
            # Nothing left to give: what remains is the shape itself, which is
            # worth more than staying under a self-imposed cap.
            return payload
        else:
            shortened = shortened or main


def _leading_records(payload: dict[str, Any], *, handle: str, budget: int) -> None:
    """Keep the most leading ``records`` that fit, at least one, saying how many were left out.

    A live data listing's records are each whole, so the ones that fit are
    worth more than none; a first record too large on its own is left to the
    caller, which leaves it out with a pointer to the handle.
    """
    records, earlier = payload["records"], payload.get("note")
    if len(records) < 2 or len(render(payload)) <= budget:
        return

    def keep(count: int) -> bool:
        dropped = MCP_MSG_DROPPED.format(dropped=len(records) - count, total=len(records))
        note = MCP_MSG_TRUNCATED.format(dropped=dropped, handle=handle)
        payload["records"] = records[:count]
        payload["note"] = f"{earlier} {note}" if earlier else note
        return len(render(payload)) <= budget

    payload["truncated"] = True
    # The most that fit, found by halving: rendering once per record dropped is quadratic on a long listing.
    low, high = 1, len(records) - 1
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if keep(middle) else (low, middle - 1)
    keep(low)


def _set_note(payload: dict[str, Any], handle: str, dropped: int, total: int, extra_note: str) -> None:
    note = ""
    if payload["truncated"]:
        dropped_note = MCP_MSG_DROPPED.format(dropped=dropped, total=total) if dropped else ""
        note = MCP_MSG_TRUNCATED.format(marker=MCP_TRUNCATION_MARKER, dropped=dropped_note, handle=handle)
    note = (note + extra_note).strip()
    if note:
        payload["note"] = note


def _shorten(item: dict[str, Any], text_keys: tuple[str, ...], limit: int) -> bool:
    """Cut every text field of one item to ``limit``. ``True`` if anything was actually cut."""
    cut = False
    for key in text_keys:
        text = item.get(key)
        if text is None:
            # Absent stays absent: an empty string would claim the backend sent one.
            continue
        item[key] = truncate(text, max(limit, 0))
        cut = cut or item[key] != text
    return cut


def _room(item: dict[str, Any], text_keys: tuple[str, ...]) -> int:
    """How long this item's longest text field is, which is what a further cut has to work with."""
    return max((len(item.get(key) or "") for key in text_keys), default=0)

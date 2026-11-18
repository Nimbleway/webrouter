"""Nimble's fixed values that :mod:`web_router.constants` does not hold, kept apart so it does not grow.

Among them is Nimble's own wording that the provider reads: the source index a research run without trust ends
its prose with.
"""

from __future__ import annotations

import re

NIMBLE_AUTH_HEADER = "Authorization"
"""The header the SDK sends a key in; a keyless call omits it, which Nimble serves on its keyless tier."""

NIMBLE_INDEX_HEADING = re.compile(r"^[ \t#*_]*source index[*_]*:?[*_]*[ \t]*$", re.IGNORECASE | re.MULTILINE)
"""The line the prose's own source list starts after, as Nimble writes it: ``Source index:``."""

NIMBLE_INDEX_ENTRY = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?\[(\d+)\][^\n]*?(https?://(?:[^\s()\]>]|\([^\s()]*\))+)", re.MULTILINE
)
"""One line of that list: its number, then the first URL on the line, keeping a balanced ``(...)`` in it."""

NIMBLE_MARKER = "[{number}]"
"""How the prose cites list entry ``number`` inline."""

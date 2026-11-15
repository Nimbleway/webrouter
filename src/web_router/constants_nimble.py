"""Nimble's own wording that the provider reads: the source index a research run without trust ends its prose with."""

from __future__ import annotations

import re

NIMBLE_INDEX_HEADING = re.compile(r"^[ \t#*_]*source index[*_]*:?[*_]*[ \t]*$", re.IGNORECASE | re.MULTILINE)
"""The line the prose's own source list starts after, as Nimble writes it: ``Source index:``."""

NIMBLE_INDEX_ENTRY = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?\[(\d+)\][^\n]*?(https?://(?:[^\s()\]>]|\([^\s()]*\))+)", re.MULTILINE
)
"""One line of that list: its number, then the first URL on the line, keeping a balanced ``(...)`` in it."""

NIMBLE_MARKER = "[{number}]"
"""How the prose cites list entry ``number`` inline."""

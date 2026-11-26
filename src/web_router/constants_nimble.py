"""Nimble's fixed values that :mod:`web_router.constants` does not hold, kept apart so it does not grow.

Among them is Nimble's own wording that the provider reads: the source index a research run without trust ends
its prose with.
"""

from __future__ import annotations

import re

NIMBLE_AUTH_HEADER = "Authorization"
"""The header the SDK sends a key in; a keyless call omits it, which Nimble serves on its keyless tier."""

MSG_NIMBLE_LITE_RECENCY = (
    "Nimble's lite depth does not filter by recency; ask for search_depth 'fast' or 'deep', leave recency unset, "
    "or name another provider."
)
"""Checked live 2026-11-24: ``recency='day'`` left lite's results unchanged and replaced standard's."""

NIMBLE_INDEX_HEADING = re.compile(r"^[ \t#*_]*source index[*_]*:?[*_]*[ \t]*$", re.IGNORECASE | re.MULTILINE)
"""The line the prose's own source list starts after, as Nimble writes it: ``Source index:``."""

NIMBLE_INDEX_ENTRY = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?\[(\d+)\][^\n]*?(https?://(?:[^\s()\]>]|\([^\s()]*\))+)", re.MULTILINE
)
"""One line of that list: its number, then the first URL on the line, keeping a balanced ``(...)`` in it."""

NIMBLE_MARKER = "[{number}]"
"""How the prose cites list entry ``number`` inline."""

NIMBLE_SDK_BASE_URL = "https://sdk.nimbleway.com"
"""The Nimble SDK's own host, given explicitly where the SDK would otherwise take ``NIMBLE_BASE_URL``."""

ENV_NIMBLE_CUSTOM_HEADERS = "NIMBLE_CUSTOM_HEADERS"
"""Headers the Nimble SDK adds from the environment, one ``Name: value`` per line; never on a client reading none."""

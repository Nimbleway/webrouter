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

NIMBLE_DOMAIN_LIMITS: dict[str, int] = {"include_domains": 400, "exclude_domains": 1000}
"""The most entries each domain list takes, as raw lists. Checked live 2026-11-26: 400 and 1,000 were served at fast
and deep, and one more answered 422 naming the field and the limit."""

NIMBLE_LITE_DOMAIN_LIMIT = 50
"""The most distinct entries either list takes at lite. Checked live 2026-11-26: 50 served, 51 answered 422."""

MSG_NIMBLE_DOMAINS = (
    f"Nimble takes at most {NIMBLE_DOMAIN_LIMITS['include_domains']:,} include_domains and "
    f"{NIMBLE_DOMAIN_LIMITS['exclude_domains']:,} exclude_domains, and {NIMBLE_LITE_DOMAIN_LIMIT} distinct entries in "
    "either at search_depth 'lite'; send fewer, or name another provider."
)

NIMBLE_INDEX_HEADING = re.compile(r"^[ \t#*_]*source index[*_]*:?[*_]*[ \t]*$", re.IGNORECASE | re.MULTILINE)
"""The line the prose's own source list starts after, as Nimble writes it: ``Source index:``."""

NIMBLE_INDEX_ENTRY = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?\[(\d+)\][^\n]*?(https?://(?:[^\s()\]>]|\([^\s()]*\))+)", re.MULTILINE
)
"""One line of that list: its number, then the first URL on the line, keeping a balanced ``(...)`` in it."""

NIMBLE_MARKER = "[{number}]"
"""How the prose cites list entry ``number`` inline."""

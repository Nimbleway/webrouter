"""The bounded text used for search descriptions and citation snippets."""

from __future__ import annotations

SNIPPET_LIMIT = 500
"""Maximum snippet length, including a trailing ellipsis."""


def as_snippet(text: str) -> str:
    """Shorten text at whitespace to at most ``SNIPPET_LIMIT`` characters, ellipsis included."""
    if len(text) <= SNIPPET_LIMIT:
        return text
    head = text[: SNIPPET_LIMIT - 1]
    cut = max(map(head.rfind, " \n\t"))
    return (head[:cut] if cut > 0 else head).rstrip() + "\u2026"

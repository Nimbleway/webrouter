"""The page tree a snapshot returns: compacted, its refs, and its cut.

Split out of :mod:`web_router.providers._page`, which AGENTS.md pins at a
line budget that may only shrink.
"""

from __future__ import annotations

import re

from web_router.constants import (
    BROWSER_REF_PATTERN,
    BROWSER_SNAPSHOT_INDENT,
    BROWSER_SNAPSHOT_URL_PREFIX,
    BROWSER_SNAPSHOT_WRAPPER_PATTERN,
    MSG_BROWSER_BAD_MAX_CHARS,
)

_REF = re.compile(BROWSER_REF_PATTERN)
_WRAPPER = re.compile(BROWSER_SNAPSHOT_WRAPPER_PATTERN)


def compact_snapshot(tree: str) -> str:
    """Drop unnamed wrapper containers and link-URL lines from a ``mode="ai"`` snapshot.

    A dropped container's children move up a level, so the tree keeps its
    shape. A line that introduced children and has none left loses its
    trailing colon. Refs are left exactly as Playwright printed them.
    """
    lines = tree.splitlines()
    kept: list[tuple[int, str, bool]] = []
    open_wrappers: list[int] = []
    for index, line in enumerate(lines):
        text = line.lstrip()
        indent = len(line) - len(text)
        while open_wrappers and indent <= open_wrappers[-1]:
            open_wrappers.pop()
        if text.startswith(BROWSER_SNAPSHOT_URL_PREFIX):
            continue
        if _WRAPPER.match(line):
            open_wrappers.append(indent)
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        had_children = len(following) - len(following.lstrip()) > indent
        kept.append((max(indent - BROWSER_SNAPSHOT_INDENT * len(open_wrappers), 0), text, had_children))

    out = []
    for index, (indent, text, had_children) in enumerate(kept):
        has_children = index + 1 < len(kept) and kept[index + 1][0] > indent
        if had_children and not has_children and text.endswith(":"):
            text = text[:-1]
        out.append(" " * indent + text)
    return "\n".join(out)


def refs_in(tree: str) -> frozenset[str]:
    """Every ref a snapshot carries."""
    return frozenset(_REF.findall(tree))


def truncate_tree(tree: str, max_chars: int | None) -> tuple[str, bool]:
    """``tree`` cut at the last line boundary within ``max_chars``, and whether anything was cut."""
    if max_chars is None:
        return tree, False
    if max_chars < 1:
        raise ValueError(MSG_BROWSER_BAD_MAX_CHARS.format(max_chars=max_chars))
    if len(tree) <= max_chars:
        return tree, False
    cut = tree.rfind("\n", 0, max_chars + 1)
    return (tree[:cut] if cut > 0 else ""), True

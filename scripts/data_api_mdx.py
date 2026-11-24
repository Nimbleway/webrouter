"""Escaping catalog text for MDX: JSX string literals inside the page blocks, entities in Markdown prose.

MDX reads braces, angle brackets and, on Mintlify, ``$`` as markup, so no vendor
string reaches a page unescaped.
"""

from __future__ import annotations

import json
import re

_ENTITIES = {
    "<": "&lt;",
    ">": "&gt;",
    "{": "&#123;",
    "}": "&#125;",
    "$": "&#36;",
    "\\": "&#92;",
}
"""Characters MDX reads as markup: tags, expressions and, on Mintlify, $math$. Entities, not backslashes,
since a backslash inside an autolinked URL is kept and the tag after it still parses."""
_UNSAFE = re.compile("[" + re.escape("".join(_ENTITIES)) + "]")


def literal(value: str) -> str:
    """A JSX string expression: nothing in it is read as Markdown, a tag or math."""
    return "{" + json.dumps(value, ensure_ascii=False).replace("$", "\\u0024") + "}"


def text(value: str) -> str:
    """A catalog string as JSX children: plain runs as string literals, `code` spans as <code>."""
    parts = value.split("`")
    if len(parts) % 2 == 0:  # an unmatched backtick is just a character
        parts = [value]
    out = []
    for index, part in enumerate(parts):
        if not part:
            continue
        out.append(f"<code>{literal(part)}</code>" if index % 2 else literal(part))
    return "".join(out)


def mdx(value: str) -> str:
    """A catalog string as Markdown prose: markup characters as entities, `code` spans kept."""
    parts = value.split("`")
    for index in range(0, len(parts), 2):  # even parts are outside backticks
        parts[index] = _UNSAFE.sub(lambda match: _ENTITIES[match.group(0)], parts[index])
    if len(parts) % 2 == 0:  # an unmatched backtick: escape the rest as prose too
        parts[-1] = _UNSAFE.sub(lambda match: _ENTITIES[match.group(0)], parts[-1])
    return "`".join(parts)


def code(value: object) -> str:
    return f"<code>{literal(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))}</code>"


def href(url: str) -> str:
    return json.dumps(url)


def anchor(endpoint_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", endpoint_id.lower()).strip("-")

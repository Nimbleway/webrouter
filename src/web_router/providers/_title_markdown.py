"""A markdown H1, excluding fenced code and retaining literal punctuation."""

from __future__ import annotations

import re
from typing import Final

_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_H1: Final = re.compile(r"^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_SETEXT: Final = re.compile(r"^ {0,3}=+[ \t]*$")
_INLINE: Final = re.compile(
    r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])"
    r"|(`+)(.+?)\2(?!`)"
    r"|!?\[([^\]\n]+)\]\("
    r"|(?<!\w)(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\5(?!\w)"
)


def page_title(content: str) -> str:
    """The first H1 outside a code fence, with inline formatting removed."""
    fence = ""
    previous = ""
    for line in content.splitlines():
        marker = _FENCE.fullmatch(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
            continue
        if marker and (marker[1][0] != "`" or "`" not in marker[2]):
            fence = marker[1]
            previous = ""
            continue
        heading = _H1.fullmatch(line)
        if heading:
            return _inline(heading[1])
        if _SETEXT.fullmatch(line) and previous.strip() and not previous.startswith(("    ", "\t")):
            return _inline(previous.strip())
        previous = line
    return ""


def _inline(text: str) -> str:
    parts: list[str] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() < position:
            continue
        parts.append(text[position : match.start()])
        position = match.end()
        if match[4] is not None:
            end = _link_end(text, position)
            parts.append(match[4] if end is not None else match[0])
            position = end if end is not None else position
        else:
            parts.append(match[1] or match[3] or match[6])
    parts.append(text[position:])
    return "".join(parts)


def _link_end(text: str, start: int) -> int | None:
    depth = 1
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    return None

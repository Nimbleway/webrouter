"""A markdown H1, excluding fenced code and retaining literal punctuation."""

from __future__ import annotations

import re
from typing import Final

_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_H1: Final = re.compile(r"^ {0,3}#[ \t]+(.*)$")
_SETEXT: Final = re.compile(r"^ {0,3}=+[ \t]*$")
_TOKEN: Final = re.compile(
    r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])"
    r"|`+|\*+|_+|!?\[|\]\(|[()]|[^\\`*_!\[\]()]+|.",
    re.DOTALL,
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
            text = heading[1].rstrip()
            unmarked = text.rstrip("#")
            if unmarked != text and (not unmarked or unmarked.endswith((" ", "\t"))):
                text = unmarked.rstrip()
            if title := _inline(text).strip():
                return title
            previous = ""
            continue
        if _SETEXT.fullmatch(line) and previous.strip() and not previous.startswith(("    ", "\t")):
            return _inline(previous.strip())
        previous = line
    return ""


def _inline(text: str) -> str:
    tokens = list(_TOKEN.finditer(text))
    code, links = _pairs(tokens)
    parts: list[str] = []
    brackets: list[int] = []
    emphasis: dict[str, list[int]] = {}
    index = 0
    while index < len(tokens):
        match = tokens[index]
        token = match[0]
        if index in code:
            closing = code[index]
            parts.append(text[match.end() : tokens[closing].start()])
            index = closing + 1
            continue
        if token in {"[", "!["}:
            brackets.append(len(parts))
        elif token == "](" and brackets and index in links:
            parts[brackets.pop()] = ""
            index = links[index] + 1
            continue
        if token[0] in {"*", "_"}:
            _emphasis(text, match, parts, emphasis)
        else:
            parts.append(match[1] if match[1] is not None else token)
        index += 1
    return "".join(parts)


def _pairs(tokens: list[re.Match[str]]) -> tuple[dict[int, int], dict[int, int]]:
    code: dict[int, int] = {}
    next_code: dict[str, int] = {}
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index][0]
        if token.startswith("`"):
            if token in next_code:
                code[index] = next_code[token]
            next_code[token] = index
    links: dict[int, int] = {}
    parentheses: list[int] = []
    for index, match in enumerate(tokens):
        if match[0] in {"(", "]("}:
            parentheses.append(index)
        elif match[0] == ")" and parentheses:
            links[parentheses.pop()] = index
    return code, links


def _emphasis(text: str, match: re.Match[str], parts: list[str], opened: dict[str, list[int]]) -> None:
    marker = match[0]
    before = text[match.start() - 1] if match.start() else " "
    after = text[match.end()] if match.end() < len(text) else " "
    stack = opened.setdefault(marker, [])
    intraword = marker.startswith("_") and before.isalnum() and after.isalnum()
    if stack and not before.isspace() and not intraword:
        parts[stack.pop()] = ""
    else:
        if not after.isspace() and not intraword:
            stack.append(len(parts))
        parts.append(marker)

"""A page's title, read from what it holds when the backend that fetched it named none.

Some backends return no title (Nimble's extract sets none), others do (Jina, Tavily). So
that ``document.title`` means the same whichever served the call, an empty one is filled
from the content: the HTML ``<title>``, then ``og:title``, then the first H1 -- of the HTML,
or of the markdown. The content is left as it came: the H1 stays in it.
"""

from __future__ import annotations

import html
import re
from typing import Final

from web_router.constants_extract import HTML_NON_TEXT, HTML_TITLE
from web_router.types import ExtractedDocument

_META: Final = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTRIBUTE: Final = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_SCRIPTS: Final = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_HTML_H1: Final = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.IGNORECASE | re.DOTALL)
_FENCE: Final = re.compile(r"^ {0,3}(```|~~~).*?(?:^ {0,3}\1|\Z)", re.MULTILINE | re.DOTALL)  # An unclosed one runs on
_MARKDOWN_H1: Final = re.compile(
    r"^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$"  # `# Title`
    r"|^ {0,3}(\S.*)\n {0,3}=+[ \t]*$",  # `Title` over `=====`
    re.MULTILINE,
)
_LINK: Final = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_EMPHASIS: Final = re.compile(r"(\*\*|__|\*|`)")


def titled(document: ExtractedDocument) -> ExtractedDocument:
    """``document`` with its title read from its content when the backend left it empty."""
    if document.title.strip():
        return document
    title = page_title(document)
    return document.model_copy(update={"title": title}) if title else document


def page_title(document: ExtractedDocument) -> str:
    """The title ``document``'s content gives: ``<title>``, ``og:title``, then the first H1; ``""`` when none."""
    if document.format == "html":
        return html_title(document) or _og_title(document.content) or _html_h1(document.content)
    if document.format == "text":
        return ""
    return _markdown_h1(document.content)


def html_title(document: ExtractedDocument) -> str:
    """The ``<title>`` of an HTML body, ``""`` for any other format or a body without one."""
    found = HTML_TITLE.search(document.content) if document.format == "html" else None
    return _clean(found.group(1)) if found else ""


def _og_title(content: str) -> str:
    for tag in _META.findall(content):
        attributes = {name.lower(): "".join(values) for name, *values in _ATTRIBUTE.findall(tag)}
        if "og:title" in (attributes.get("property", "").lower(), attributes.get("name", "").lower()):
            return _clean(attributes.get("content", ""))
    return ""


def _html_h1(content: str) -> str:
    found = _HTML_H1.search(_SCRIPTS.sub(" ", content))  # Not an H1 a script only writes
    return _clean(HTML_NON_TEXT.sub(" ", found.group(1))) if found else ""


def _markdown_h1(content: str) -> str:
    found = _MARKDOWN_H1.search(_FENCE.sub("", content))
    if not found:
        return ""
    return _clean(_EMPHASIS.sub("", _LINK.sub(r"\1", found.group(1) or found.group(2))))


def _clean(text: str) -> str:  # Entities decoded, runs of whitespace one space
    return " ".join(html.unescape(text).split())

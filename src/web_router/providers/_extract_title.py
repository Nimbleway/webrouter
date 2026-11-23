"""A page's title, read from what it holds when the backend that fetched it named none.

Some backends return no title (Nimble's extract sets none), others do (Jina, Tavily). So
that ``document.title`` means the same whichever served the call, an empty one is filled
from the content: the HTML ``<title>``, then ``og:title``, then the first H1 -- of the HTML,
or of the markdown. The content is left as it came: the H1 stays in it.
"""

from __future__ import annotations

import html

from web_router.constants_extract import HTML_TITLE
from web_router.providers import _title_html, _title_markdown
from web_router.types import ExtractedDocument


def titled(document: ExtractedDocument) -> ExtractedDocument:
    """``document`` with its title read from its content when the backend left it empty."""
    if document.title.strip():
        return document
    title = page_title(document)
    return document.model_copy(update={"title": title}) if title else document


def page_title(document: ExtractedDocument) -> str:
    """The title ``document``'s content gives: ``<title>``, ``og:title``, then the first H1; ``""`` when none."""
    if document.format == "html":
        return _title_html.page_title(document.content)
    if document.format == "text":
        return ""
    return _clean(_title_markdown.page_title(document.content))


def html_title(document: ExtractedDocument) -> str:
    """The ``<title>`` of an HTML body, ``""`` for any other format or a body without one."""
    found = HTML_TITLE.search(document.content) if document.format == "html" else None
    return _clean(found.group(1)) if found else ""


def _clean(text: str) -> str:  # Entities decoded, runs of whitespace one space
    return " ".join(html.unescape(text).split())

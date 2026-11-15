"""A page that is not the page asked for is a failed fetch, not a document: so ``auto`` tries the next backend for it.

Every backend's answer passes through :func:`split_error_pages`, so the rule
is the same whichever one served the call: an error status, a bot challenge
or a login wall lands in ``failures`` naming why, and a near-empty page stays
a document carrying a warning.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from http import HTTPStatus
from urllib.parse import urldefrag, urlsplit, urlunsplit

from web_router.constants import MSG_EXTRACT_BAD_STATUS
from web_router.constants_extract import (
    EXTRACT_CHALLENGE_MARKERS,
    EXTRACT_CHALLENGE_TITLE,
    EXTRACT_LOGIN_MARKERS,
    EXTRACT_LOGIN_TITLE,
    EXTRACT_LOGIN_URL_PARTS,
    EXTRACT_NEAR_EMPTY_CHARS,
    EXTRACT_WALL_MAX_CHARS,
    HTML_TITLE,
    MSG_EXTRACT_CHALLENGE,
    MSG_EXTRACT_LOGIN_WALL,
    MSG_EXTRACT_NEAR_EMPTY,
)
from web_router.types import ExtractedDocument, ExtractFailure
from web_router.types_errors import ERROR_KIND_TARGET, ERROR_KIND_UPSTREAM, TARGET_GONE_STATUSES


def _failure(document: ExtractedDocument, reason: str, asked: str | None = None) -> ExtractFailure:
    """A page that is not the one asked for, as a failure: the site's own "gone" is ``target``, else ``upstream``.

    A 404 or 410 is the site saying the page is not there, and every backend
    would read the same; a challenge, a login wall or a 5xx is what this
    backend met, and another may get past it. A 404 reached by a redirect from
    ``asked`` is the landing page's, so it stays ``upstream``.
    """
    status = document.status_code
    # A redirect that landed on an error page says nothing certain about the URL asked for: another backend may read it.
    redirected = asked is not None and _bare(asked) != _bare(document.url)
    gone = status in TARGET_GONE_STATUSES and not redirected
    kind = ERROR_KIND_TARGET if gone else ERROR_KIND_UPSTREAM
    return ExtractFailure(url=asked or document.url, error=reason, kind=kind, status_code=status)


def _bare(url: str) -> str:
    """``url`` as a backend may echo what it fetched: scheme and host lower-cased, no fragment, no trailing slash."""
    parts = urlsplit(urldefrag(url).url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def split_error_pages(
    documents: Iterable[ExtractedDocument], asked: str | None = None
) -> tuple[list[ExtractedDocument], list[ExtractFailure]]:
    """The documents worth returning, and each page that is not the one asked for as a failure saying why.

    A page with a 4xx or 5xx status fails whatever it says: a site's "not
    found" page read as content is the error the caller most needs to see. A
    near-empty page is kept, with :data:`MSG_EXTRACT_NEAR_EMPTY` in its
    ``warnings``, since some real answers are that short.

    ``asked`` is the one URL the call carried, when it carried one: a failure is
    named by it rather than by where a redirect landed, so the caller sees the
    address they asked for and ``auto`` retries that address.
    """
    kept: list[ExtractedDocument] = []
    failed: list[ExtractFailure] = []
    for document in documents:
        reason = not_the_page(document)
        if reason is not None:
            failed.append(_failure(document, reason, asked))
        else:
            kept.append(_warned(document))
    return kept, failed


def not_the_page(document: ExtractedDocument) -> str | None:
    """Why ``document`` is not the page asked for -- an error status, a challenge, a login wall -- or ``None``."""
    status = document.status_code
    if status is not None and status >= HTTPStatus.BAD_REQUEST:
        return MSG_EXTRACT_BAD_STATUS.format(status=status)
    if _is_login_path(urlsplit(document.url).path):
        return MSG_EXTRACT_LOGIN_WALL
    if len(document.content) >= EXTRACT_WALL_MAX_CHARS:
        return None
    title = _title(document)
    if EXTRACT_CHALLENGE_TITLE.search(title):
        return MSG_EXTRACT_CHALLENGE
    if EXTRACT_LOGIN_TITLE.search(title):
        return MSG_EXTRACT_LOGIN_WALL
    text = document.content.casefold()
    if any(marker in text for marker in EXTRACT_CHALLENGE_MARKERS):
        return MSG_EXTRACT_CHALLENGE
    if any(marker in text for marker in EXTRACT_LOGIN_MARKERS):
        return MSG_EXTRACT_LOGIN_WALL
    return None


def _is_login_path(path: str) -> bool:
    """Whether ``path`` is a login route itself or sits under one, not merely a longer name sharing its start."""
    return any(path == part or path.startswith(f"{part}/") for part in EXTRACT_LOGIN_URL_PARTS)


def _title(document: ExtractedDocument) -> str:
    """The page's title: the backend's, else the ``<title>`` of an HTML body, since some backends set none for HTML."""
    if document.title or document.format != "html":
        return document.title
    found = HTML_TITLE.search(document.content)
    return html.unescape(found.group(1)).strip() if found else ""


HTML_NON_TEXT = re.compile(r"<(script|style)\b.*?</\1>|<[^>]*>", re.IGNORECASE | re.DOTALL)


def near_empty(document: ExtractedDocument) -> bool:
    """Some text, but under :data:`EXTRACT_NEAR_EMPTY_CHARS` characters of it: a shell more often than a page."""
    content = document.content
    if document.format == "html":
        content = html.unescape(HTML_NON_TEXT.sub(" ", content))
    return 0 < len(content.strip()) < EXTRACT_NEAR_EMPTY_CHARS


def _warned(document: ExtractedDocument) -> ExtractedDocument:
    if not near_empty(document) or MSG_EXTRACT_NEAR_EMPTY in document.warnings:
        return document
    return document.model_copy(update={"warnings": [*document.warnings, MSG_EXTRACT_NEAR_EMPTY]})

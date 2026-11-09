"""An error page with no text is a failed fetch, not a document: so ``auto`` tries the next backend for it."""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus

from web_router.constants import MSG_EXTRACT_BAD_STATUS
from web_router.types import ExtractedDocument, ExtractFailure


def split_error_pages(documents: Iterable[ExtractedDocument]) -> tuple[list[ExtractedDocument], list[ExtractFailure]]:
    """The documents worth returning, and each empty 4xx or 5xx page as a failure naming its status.

    A 404 that came back as a document with no content read as a successful,
    empty page. One with text -- a site's own "not found" page -- is still a
    document: what it says may be the answer.
    """
    kept: list[ExtractedDocument] = []
    failed: list[ExtractFailure] = []
    for document in documents:
        status = document.status_code
        if status is not None and status >= HTTPStatus.BAD_REQUEST and not document.content.strip():
            failed.append(ExtractFailure(url=document.url, error=MSG_EXTRACT_BAD_STATUS.format(status=status)))
        else:
            kept.append(document)
    return kept, failed

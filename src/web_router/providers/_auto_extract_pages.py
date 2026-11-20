"""Which requested URL each page of an extract answer is for, and which pages ``auto`` fails on down its chain.

Kept apart from :mod:`._auto_extract_chain`, which walks the chain with them, so that file stops growing.
"""

from __future__ import annotations

from web_router.constants_extract import MSG_EXTRACT_NEAR_EMPTY, MSG_EXTRACT_OFF_SITE, MSG_EXTRACT_TRACKER_PIXEL
from web_router.providers._url_match import asked_for, owners
from web_router.types import ExtractedDocument, ExtractFailure, ExtractResponse
from web_router.types_errors import ERROR_KIND_UPSTREAM

_FAIL_OVER = frozenset({MSG_EXTRACT_TRACKER_PIXEL, MSG_EXTRACT_OFF_SITE})
"""The page warnings that fail its URL on to the next member even when none is left."""

Held = dict[str, ExtractedDocument]
"""The near-empty page each URL first got, by URL, put back when no later member reads it."""


def _screened(response: ExtractResponse, urls: list[str], more: bool, held: Held) -> ExtractResponse:
    """``response`` with each junk page as its URL's failure, so the members after it try that URL.

    A tracking pixel or a page on another site fails whatever is left; a near-empty page only when ``more``
    members are, and it is ``held`` so that :func:`_restored` can put it back. A page whose URL cannot be told stays.
    """
    documents, failures = [], list(response.failures)
    for document, url in zip(response.documents, _asked(response, urls), strict=True):
        why = [w for w in document.warnings if w in _FAIL_OVER or (more and w == MSG_EXTRACT_NEAR_EMPTY)]
        if url is None or not why:
            documents.append(document)
            continue
        if not set(why) & _FAIL_OVER:
            held.setdefault(url, document)
        error = "; ".join(f"[{response.provider}] {reason}" for reason in why)
        status = document.status_code
        failures.append(ExtractFailure(url=url, error=error, kind=ERROR_KIND_UPSTREAM, status_code=status))
    return response.model_copy(update={"documents": documents, "failures": failures})


def _restored(response: ExtractResponse, held: Held, urls: list[str]) -> ExtractResponse:
    """``response`` with each held near-empty page back in place of its URL's failure: no later member read it."""
    back = {failure.url for failure in response.failures} & held.keys()
    if not back:
        return response
    position: dict[str | None, int] = {url: index for index, url in enumerate(urls)}
    placed = [*zip(response.documents, _asked(response, urls), strict=True), *((held[url], url) for url in back)]
    return response.model_copy(
        update={
            "documents": [document for document, url in sorted(placed, key=lambda p: position.get(p[1], len(urls)))],
            "failures": [failure for failure in response.failures if failure.url not in back],
        }
    )


def _asked(first: ExtractResponse, urls: list[str]) -> list[str | None]:
    """The requested URL each page of ``first`` answers, so a page echoed under another URL keeps its place.

    Nimble and Keenable echo the URL they resolved to (a slash added to a bare host, a redirect followed, a
    fragment dropped), and Jina adds the slash. Told as :func:`~._url_match.owners` tells it; the pages it cannot
    tell (two that both answer where a redirect landed) take the URLs ``first`` neither failed nor matched, in
    turn, when there is one for each: a one-URL-per-call member answers in the order asked. Otherwise such a page
    has no place and sorts last.

    A place a page did not get from its own URL is a deduction, and it holds only while the pages stand in the
    order they came in. A member that read one URL and echoed another one asked for would have it swap two pages,
    so each page then keeps only what its own URL tells (:func:`~._url_match.asked_for`).
    """
    echoed = [document.url for document in first.documents]
    failed = [failure.url for failure in first.failures]
    named = owners(echoed, urls, failed)
    taken = {*failed, *named}
    left = [url for url in urls if url not in taken]
    if named.count(None) == len(left):
        turn = iter(left)
        named = [owner or next(turn) for owner in named]
    position = {url: index for index, url in enumerate(urls)}
    places = [position[owner] for owner in named if owner is not None]
    if places == sorted(places):
        return named
    return [next(iter(asked_for(url, urls)), None) for url in echoed]

"""Extract ``auto`` down its chain: the first member that answers, then each URL it failed on the members after it.

Kept apart from :mod:`web_router.providers.auto`, which calls it, so that file stops growing.

A batch can partly succeed on one backend: Jina reads the docs page and is
refused GitHub. Returning that answer as it came would leave the GitHub URL
failed while a later member could have read it, so the URLs the answer failed
are sent on down the chain, each pass to the members after the one that
answered the last. The response keeps the first answering member as its
``provider``; a URL no later member reads stays in ``failures`` with the
reason from each member that answered for it.
"""

from __future__ import annotations

from dataclasses import replace

from web_router.constants_extract import MSG_EXTRACT_RETRY_BLANK
from web_router.exceptions import DeadlineExceededError, WebRouterError
from web_router.providers._attempt_loop import Order, Walk, arun, run
from web_router.providers._chain import _empty_extract
from web_router.providers._url_match import asked_for, owners
from web_router.routing.attempts import contributed
from web_router.routing.metered import combined_cost
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_LOCAL, ERROR_KIND_TARGET, ERROR_KIND_TIMEOUT


def extract_down_chain(walk: Walk, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
    """The first member's answer, with each URL it failed retried on the members after it."""
    response, rest = _first_answer(walk, request, urls)
    while rest and (retry := _failed_urls(response)):
        try:
            retried, rest = _first_answer(replace(walk, order=rest), request, retry)
        except WebRouterError as exc:
            if getattr(exc, "kind", None) == ERROR_KIND_LOCAL:
                raise
            return _also_failed(response, exc)
        if not _empty_extract(retried):
            contributed("extract", retried.provider)
        response = _merged(response, retried, urls, request)
    return response


async def aextract_down_chain(walk: Walk, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
    """Async counterpart of :func:`extract_down_chain`."""
    response, rest = await _afirst_answer(walk, request, urls)
    while rest and (retry := _failed_urls(response)):
        try:
            retried, rest = await _afirst_answer(replace(walk, order=rest), request, retry)
        except WebRouterError as exc:
            if getattr(exc, "kind", None) == ERROR_KIND_LOCAL:
                raise
            return _also_failed(response, exc)
        if not _empty_extract(retried):
            contributed("extract", retried.provider)
        response = _merged(response, retried, urls, request)
    return response


def _first_answer(walk: Walk, request: ExtractRequest, urls: list[str]) -> tuple[ExtractResponse, Order]:
    """The first member's answer for ``urls``, and the members after it; raises when none answers."""
    ran = run(walk, request.model_copy(update={"urls": urls}))
    return ran.response, ran.rest


async def _afirst_answer(walk: Walk, request: ExtractRequest, urls: list[str]) -> tuple[ExtractResponse, Order]:
    """Async counterpart of :func:`_first_answer`."""
    ran = await arun(walk, request.model_copy(update={"urls": urls}))
    return ran.response, ran.rest


def _failed_urls(response: ExtractResponse) -> list[str]:
    """The URLs worth another backend: not those whose failure is the page's own (a 404, an unresolvable host)."""
    return list(dict.fromkeys(failure.url for failure in response.failures if failure.kind != ERROR_KIND_TARGET))


def _also_failed(response: ExtractResponse, exc: WebRouterError) -> ExtractResponse:
    """``response`` with why no later member read its retried URLs added to each one's reason, its kind the latest.

    The kind and the status are replaced together, so both describe the same failure: an earlier backend's 429
    never stays beside a later ``target``. A deadline carries no status, so its URLs are left with none.
    """
    retried = set(_failed_urls(response))
    kind = ERROR_KIND_TIMEOUT if isinstance(exc, DeadlineExceededError) else getattr(exc, "kind", None)
    latest = {} if kind is None else {"kind": kind, "status_code": getattr(exc, "status_code", None)}
    failures = [
        failure.model_copy(update={"error": f"{failure.error}; then {exc}", **latest})
        if failure.url in retried
        else failure
        for failure in response.failures
    ]
    return response.model_copy(update={"failures": failures})


def _merged(
    first: ExtractResponse, retried: ExtractResponse, urls: list[str], request: ExtractRequest
) -> ExtractResponse:
    """``first`` with what ``retried`` read added, in request order; a URL that failed both keeps both reasons.

    Each page ``retried`` returned is matched to the URL it was sent for (:func:`~._url_match.owners`); a blank
    one was not read, and a page or failure no sent URL can be told for is left out: the caller never asked for
    it, and a later member would fetch it. The cost is both calls' (:func:`~web_router.routing.metered.combined_cost`).
    """
    sent = _failed_urls(first)  # only these went on the retry: not a 404 or other page-own failure
    blank = MSG_EXTRACT_RETRY_BLANK.format(provider=retried.provider)
    named = owners([document.url for document in retried.documents], sent, [f.url for f in retried.failures])
    pages = list(zip(retried.documents, named, strict=True))
    # Each read page sorts by the URL it answers, not where a redirect left it.
    read = [(document, owner) for document, owner in pages if owner and document.content.strip()]
    blanks = [ExtractFailure(url=owner or d.url, error=blank) for d, owner in pages if not d.content.strip()]
    again = {
        asked: failure
        for failure in [*blanks, *retried.failures]
        for asked in asked_for(failure.url, sent) or {failure.url}
    }
    found = {owner for _, owner in read}
    failures = [
        failure.model_copy(
            update={
                "error": f"{failure.error}; then {again[failure.url].error}",
                "kind": again[failure.url].kind,
                "status_code": again[failure.url].status_code,
            }
        )
        if failure.url in again
        else failure
        for failure in first.failures
        if failure.url in again or failure.url not in found
    ]
    position = {url: index for index, url in enumerate(urls)}
    placed = [*((document, document.url) for document in first.documents), *read]
    documents = [document for document, url in sorted(placed, key=lambda pair: position.get(pair[1], len(urls)))]
    return first.model_copy(
        update={
            "documents": documents,
            "failures": failures,
            "raw": [*(first.raw or []), *(retried.raw or [])] or None,
            **combined_cost("extract", [(first, _for(request, urls)), (retried, _for(request, sent))]),
        }
    )


def _for(request: ExtractRequest, urls: list[str]) -> ExtractRequest:
    return request.model_copy(update={"urls": urls})

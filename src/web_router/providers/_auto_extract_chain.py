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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from web_router.constants_extract import MSG_EXTRACT_RETRY_BLANK
from web_router.exceptions import DeadlineExceededError, WebRouterError
from web_router.providers._chain import _RECOVERABLE, _empty_extract, _held_or_raise
from web_router.providers._chain_deadline import _held_or_late, _late_answer
from web_router.providers._hop import ahop, hop
from web_router.providers._url_match import asked_for, owners
from web_router.providers.base import ExtractProvider
from web_router.routing.attempts import admitted, contributed, failed
from web_router.routing.metered import combined_cost
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse
from web_router.types_errors import ERROR_KIND_LOCAL, ERROR_KIND_TARGET, ERROR_KIND_TIMEOUT

_Order = list[tuple[int, str]]


@dataclass(frozen=True)
class _Walk:
    """What a walk down ``auto``'s chain needs from it: its name, whether an empty answer moves on, its members."""

    name: str
    fallback_on_empty: bool
    member: Callable[[int], ExtractProvider]
    """Builds, or returns the cached, member at a chain position."""
    order: _Order
    """The members still to try, as (chain position, name), in call order."""
    built: Mapping[int, ExtractProvider] = field(default_factory=dict)
    """The members built so far, by chain position: a failure's says whether it held a credential to refuse."""


def extract_down_chain(walk: _Walk, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
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


async def aextract_down_chain(walk: _Walk, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
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


def _first_answer(walk: _Walk, request: ExtractRequest, urls: list[str]) -> tuple[ExtractResponse, _Order]:
    """The first member's answer for ``urls``, and the members after it; raises when none answers."""
    delegated = request.model_copy(update={"urls": urls})
    failures: dict[str, WebRouterError] = {}
    held: ExtractResponse | None = None
    for index, (position, provider_name) in enumerate(walk.order):
        if (late := _late_answer(held, walk.name, failures)) is not None:
            return late, []
        if not admitted("extract", provider_name, delegated):
            continue
        try:
            response = hop("extract", provider_name, walk.member(position).extract, delegated)
        except DeadlineExceededError as exc:
            return _held_or_late(held, walk.name, failures, exc, provider_name, "extract"), []
        except _RECOVERABLE as exc:
            failed(failures, "extract", provider_name, exc, walk.built.get(position))
            continue
        if not (walk.fallback_on_empty and _empty_extract(response)):
            return response, walk.order[index + 1 :]
        held = response if held is None else held
    return _held_or_raise(held, walk.name, failures), []


async def _afirst_answer(walk: _Walk, request: ExtractRequest, urls: list[str]) -> tuple[ExtractResponse, _Order]:
    """Async counterpart of :func:`_first_answer`."""
    delegated = request.model_copy(update={"urls": urls})
    failures: dict[str, WebRouterError] = {}
    held: ExtractResponse | None = None
    for index, (position, provider_name) in enumerate(walk.order):
        if (late := _late_answer(held, walk.name, failures)) is not None:
            return late, []
        if not admitted("extract", provider_name, delegated):
            continue
        try:
            response = await ahop("extract", provider_name, walk.member(position).aextract, delegated)
        except DeadlineExceededError as exc:
            return _held_or_late(held, walk.name, failures, exc, provider_name, "extract"), []
        except _RECOVERABLE as exc:
            failed(failures, "extract", provider_name, exc, walk.built.get(position))
            continue
        if not (walk.fallback_on_empty and _empty_extract(response)):
            return response, walk.order[index + 1 :]
        held = response if held is None else held
    return _held_or_raise(held, walk.name, failures), []


def _failed_urls(response: ExtractResponse) -> list[str]:
    """The URLs worth another backend: not those whose failure is the page's own (a 404, an unresolvable host)."""
    return list(dict.fromkeys(failure.url for failure in response.failures if failure.kind != ERROR_KIND_TARGET))


def _also_failed(response: ExtractResponse, exc: WebRouterError) -> ExtractResponse:
    """``response`` with why no later member read its retried URLs added to each one's reason, its kind the latest."""
    retried = set(_failed_urls(response))
    kind = ERROR_KIND_TIMEOUT if isinstance(exc, DeadlineExceededError) else getattr(exc, "kind", None)
    # Only what the later failure knows replaces the earlier: a deadline carries no status of its own.
    latest = {
        field: value
        for field, value in (("kind", kind), ("status_code", getattr(exc, "status_code", None)))
        if value is not None
    }
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
    one was not read, and one no sent URL can be told for is left out rather than returned beside that URL's
    failure. The cost is both calls' (:func:`~web_router.routing.metered.combined_cost`).
    """
    sent = [failure.url for failure in first.failures]  # only these went on the retry
    blank = MSG_EXTRACT_RETRY_BLANK.format(provider=retried.provider)
    named = owners([document.url for document in retried.documents], sent, [f.url for f in retried.failures])
    pages = list(zip(retried.documents, named, strict=True))
    read = [document for document, owner in pages if owner and document.content.strip()]
    blanks = [ExtractFailure(url=owner or d.url, error=blank) for d, owner in pages if not d.content.strip()]
    again = {
        asked: failure
        for failure in [*blanks, *retried.failures]
        for asked in asked_for(failure.url, sent) or {failure.url}
    }
    found = {owner for document, owner in pages if owner and document.content.strip()}
    failures = [
        failure.model_copy(
            update={
                "error": f"{failure.error}; then {again[failure.url].error}",
                "kind": again[failure.url].kind,
                "status_code": again[failure.url].status_code or failure.status_code,
            }
        )
        if failure.url in again
        else failure
        for failure in first.failures
        if failure.url in again or failure.url not in found
    ]
    known = {failure.url for failure in first.failures}
    failures.extend(failure for failure in retried.failures if not (asked_for(failure.url, sent) & known))
    position = {url: index for index, url in enumerate(urls)}
    documents = sorted([*first.documents, *read], key=lambda document: position.get(document.url, len(urls)))
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

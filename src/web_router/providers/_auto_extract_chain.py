"""Extract ``auto`` down its chain: the first member that answers, then each URL it failed on the members after it.

Kept apart from :mod:`web_router.providers.auto`, which calls it from
``AutoExtractProvider._extract``, so that file stops growing.

A batch can partly succeed on one backend: Jina reads the docs page and is
refused GitHub. Returning that answer as it came would leave the GitHub URL
failed while a later member could have read it, so the URLs the answer failed
are sent on down the chain, each pass to the members after the one that
answered the last. The response keeps the first answering member as its
``provider``; a URL no later member reads stays in ``failures`` with the
reason from each member that answered for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from web_router.exceptions import DeadlineExceededError, WebRouterError
from web_router.providers._chain import _RECOVERABLE, _empty_extract, _held_or_raise
from web_router.providers._chain_deadline import _held_or_late, _late_answer
from web_router.routing.attempts import admitted, failed
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse

if TYPE_CHECKING:
    from web_router.providers.auto import AutoExtractProvider

_Order = list[tuple[int, str]]


def extract_down_chain(
    auto: AutoExtractProvider, order: _Order, request: ExtractRequest, urls: list[str]
) -> ExtractResponse:
    """The first member's answer, with each URL it failed retried on the members after it."""
    response, rest = _first_answer(auto, order, request, urls)
    while rest and (retry := _failed_urls(response)):
        try:
            retried, rest = _first_answer(auto, rest, request, retry)
        except WebRouterError as exc:
            return _also_failed(response, exc)
        response = merged(response, retried, urls)
    return response


async def aextract_down_chain(
    auto: AutoExtractProvider, order: _Order, request: ExtractRequest, urls: list[str]
) -> ExtractResponse:
    """Async counterpart of :func:`extract_down_chain`."""
    response, rest = await _afirst_answer(auto, order, request, urls)
    while rest and (retry := _failed_urls(response)):
        try:
            retried, rest = await _afirst_answer(auto, rest, request, retry)
        except WebRouterError as exc:
            return _also_failed(response, exc)
        response = merged(response, retried, urls)
    return response


def _first_answer(
    auto: AutoExtractProvider, order: _Order, request: ExtractRequest, urls: list[str]
) -> tuple[ExtractResponse, _Order]:
    """The first member's answer for ``urls``, and the members after it; raises when none answers."""
    delegated = auto._delegated(request, urls)
    failures: dict[str, WebRouterError] = {}
    held: ExtractResponse | None = None
    for index, (position, provider_name) in enumerate(order):
        if (late := _late_answer(held, auto.name, failures)) is not None:
            return late, []
        if not admitted("extract", provider_name, delegated):
            continue
        try:
            response = auto._member(position).extract(delegated)
        except DeadlineExceededError as exc:
            return _held_or_late(held, auto.name, failures, exc, provider_name, "extract"), []
        except _RECOVERABLE as exc:
            failed(failures, "extract", provider_name, exc)
            continue
        if not (auto.fallback_on_empty and _empty_extract(response)):
            return response, order[index + 1 :]
        held = response if held is None else held
    return _held_or_raise(held, auto.name, failures), []


async def _afirst_answer(
    auto: AutoExtractProvider, order: _Order, request: ExtractRequest, urls: list[str]
) -> tuple[ExtractResponse, _Order]:
    """Async counterpart of :func:`_first_answer`."""
    delegated = auto._delegated(request, urls)
    failures: dict[str, WebRouterError] = {}
    held: ExtractResponse | None = None
    for index, (position, provider_name) in enumerate(order):
        if (late := _late_answer(held, auto.name, failures)) is not None:
            return late, []
        if not admitted("extract", provider_name, delegated):
            continue
        try:
            response = await auto._member(position).aextract(delegated)
        except DeadlineExceededError as exc:
            return _held_or_late(held, auto.name, failures, exc, provider_name, "extract"), []
        except _RECOVERABLE as exc:
            failed(failures, "extract", provider_name, exc)
            continue
        if not (auto.fallback_on_empty and _empty_extract(response)):
            return response, order[index + 1 :]
        held = response if held is None else held
    return _held_or_raise(held, auto.name, failures), []


def _failed_urls(response: ExtractResponse) -> list[str]:
    return list(dict.fromkeys(failure.url for failure in response.failures))


def _also_failed(response: ExtractResponse, exc: WebRouterError) -> ExtractResponse:
    """``response`` with why no later member read its failed URLs added to each one's reason."""
    failures = [ExtractFailure(url=failure.url, error=f"{failure.error}; then {exc}") for failure in response.failures]
    return response.model_copy(update={"failures": failures})


def merged(first: ExtractResponse, retried: ExtractResponse, urls: list[str]) -> ExtractResponse:
    """``first`` with what ``retried`` read added, in request order; a URL that failed both keeps both reasons."""
    again = {failure.url: failure.error for failure in retried.failures}
    failures = [
        ExtractFailure(url=failure.url, error=f"{failure.error}; then {again[failure.url]}")
        for failure in first.failures
        if failure.url in again
    ]
    known = {failure.url for failure in first.failures}
    failures.extend(failure for failure in retried.failures if failure.url not in known)
    position = {url: index for index, url in enumerate(urls)}
    documents = sorted(
        [*first.documents, *retried.documents], key=lambda document: position.get(document.url, len(urls))
    )
    return first.model_copy(
        update={
            "documents": documents,
            "failures": failures,
            "raw": [*(first.raw or []), *(retried.raw or [])] or None,
        }
    )

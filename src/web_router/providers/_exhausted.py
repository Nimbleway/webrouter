"""The error an extract raises when every URL failed across more than one call."""

from __future__ import annotations

from collections.abc import Sequence

from web_router.constants import MSG_EXTRACT_NOTHING_RETURNED
from web_router.constants_production import COOLDOWN_STATUSES
from web_router.exceptions_typed import ExtractFailedError, shared_status
from web_router.types_errors import ERROR_KIND_RATE_LIMIT, ExtractFailure


def nothing_returned(
    provider: str, failures: Sequence[ExtractFailure], errors: Sequence[tuple[list[str], BaseException]]
) -> ExtractFailedError:
    """One error naming each failed URL and carrying its record; it keeps every call's own error, so a throttle counts.

    Its ``status_code`` is the one every URL failed with, as its ``kind`` is, so a one-URL call that met a 404
    page raises with the 404 a batch would report for that URL.
    """
    calls = tuple(error for _, error in errors)
    detail = "; ".join(f"{failure.url}: {failure.error}" for failure in failures)
    original = ExtractFailure.shared_original(failures, list(calls))
    message = MSG_EXTRACT_NOTHING_RETURNED.format(failures=detail)
    error = ExtractFailedError(provider, message, failures, original=original)
    error.call_errors = calls
    # A page's own status (a 404 page) raises no call error to read it from: the failures carry it.
    error.status_code = error.status_code or shared_status(map(_page_status, failures))
    return error


def _page_status(failure: ExtractFailure) -> int | None:
    """The status ``failure`` lends the call's error: none for a page the site throttled.

    A 429 or 402 on the error itself reads as the backend's own throttle and cools it down for every call, as
    :meth:`~web_router.providers._cooldown.Cooldowns` does for a partial answer only on a ``rate_limit`` failure.
    """
    if failure.status_code in COOLDOWN_STATUSES and failure.kind != ERROR_KIND_RATE_LIMIT:
        return None
    return failure.status_code

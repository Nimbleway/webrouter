"""The error an extract raises when every URL failed across more than one call."""

from __future__ import annotations

from collections.abc import Sequence

from web_router.constants import MSG_EXTRACT_NOTHING_RETURNED
from web_router.exceptions_typed import ExtractFailedError, shared_status
from web_router.types_errors import ExtractFailure


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
    error.status_code = error.status_code or shared_status(failure.status_code for failure in failures)
    return error

"""The error an extract raises when every URL failed across more than one call."""

from __future__ import annotations

from collections.abc import Sequence

from web_router.constants import MSG_EXTRACT_NOTHING_RETURNED
from web_router.exceptions import ProviderError
from web_router.types_errors import ExtractFailure


def nothing_returned(
    provider: str, failures: Sequence[ExtractFailure], errors: Sequence[tuple[list[str], BaseException]]
) -> ProviderError:
    """One error naming each failed URL; it keeps every call's own error, so a throttle on any one still counts."""
    calls = tuple(error for _, error in errors)
    detail = "; ".join(f"{failure.url}: {failure.error}" for failure in failures)
    original = ExtractFailure.shared_original(failures, list(calls))
    message = MSG_EXTRACT_NOTHING_RETURNED.format(failures=detail)
    error = ProviderError(provider, message, original=original, kind=ExtractFailure.batch_kind(failures))
    error.call_errors = calls
    return error

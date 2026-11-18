"""A failure in one short line, for a model, a CLI's stderr or a log.

A backend's own error text can run to thousands of characters -- a sign-up
link, payment instructions, a whole HTML error page -- with the cause buried in
the middle. :func:`short_error` says what failed from the error's fields
instead, and the vendor's text stays on the exception for whoever wants it.

    from web_router.integrations import short_error

    try:
        router.search(query)
    except WebRouterError as exc:
        print(short_error(exc), file=sys.stderr)
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.constants import INTEGRATION_RESULT_CHARS
from web_router.exceptions import DeadlineExceededError, ProviderError
from web_router.integrations._messages import (
    FACT_RETRY_AFTER,
    FACT_STATUS,
    FACTS,
    GENERIC_REASON,
    KIND_REASONS,
    SHORT_ERROR,
    TRIED,
)
from web_router.integrations._render import cut_to_budget
from web_router.types_errors import is_retryable

TOOL_FAILURES = (ProviderError, DeadlineExceededError)
"""What a tool turns into an error result for the model: the backend failing, or the call running out of time.

Every other error is the caller's setup -- a missing key, an option the
provider does not map -- and raises, because no retry by the model fixes it.
"""


def short_error(exc: BaseException, *, limit: int = INTEGRATION_RESULT_CHARS) -> str:
    """``exc`` in one line, cut at ``limit`` characters and marked where cut: who failed, why, and whether to retry.

    A backend's failure is told from its fields -- ``kind``, ``retryable``,
    ``status_code``, ``retry_after`` and, for ``auto``, each member's kind --
    never from the vendor's text. An error without a ``kind`` (a setup
    mistake, whose message is web-router's own) is its message's first line.
    """
    kind = getattr(exc, "kind", None)
    if not isinstance(kind, str):
        lines = str(exc).strip().splitlines()
        return cut_to_budget(lines[0] if lines else type(exc).__name__, limit)
    provider = getattr(exc, "provider", None)
    text = SHORT_ERROR.format(
        who=f"{provider}: " if isinstance(provider, str) else "",
        reason=KIND_REASONS.get(kind, GENERIC_REASON),
        facts=error_facts(exc),
    )
    return cut_to_budget(text + _tried(getattr(exc, "failures", None)), limit)


def error_facts(exc: BaseException) -> str:
    """``kind=..., retryable=...``, then the status and when to retry where the error knows them."""
    kind = getattr(exc, "kind", None)
    retryable = getattr(exc, "retryable", None)
    retryable = retryable if isinstance(retryable, bool) else is_retryable(kind)
    facts = FACTS.format(kind=kind, retryable=str(retryable).lower())
    if isinstance(status := getattr(exc, "status_code", None), int):
        facts += FACT_STATUS.format(status=status)
    if isinstance(delay := getattr(exc, "retry_after", None), int | float):
        facts += FACT_RETRY_AFTER.format(seconds=delay)
    return facts


def _tried(failures: object) -> str:
    """`` Tried: a (kind), b (kind).`` for an error that sums up several backends' failures, else nothing."""
    if not isinstance(failures, Mapping) or not failures:
        return ""
    members = ", ".join(f"{name} ({getattr(error, 'kind', None) or 'error'})" for name, error in failures.items())
    return TRIED.format(members=members)


__all__ = ["short_error"]

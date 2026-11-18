"""Exceptions a caller can branch on by their fields: a refused option, and every backend of a chain failing.

Kept apart from :mod:`web_router.exceptions`, so that file stops growing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from web_router.constants_errors import MSG_INVALID_PARAMETER
from web_router.exceptions import ProviderError, WebRouterError
from web_router.types_errors import ERROR_KIND_UPSTREAM, ExtractFailure


class InvalidParameterError(WebRouterError, ValueError):
    """A request option is not a value the method takes: an empty query, ``max_results=0``, an unknown level.

    Raised before any backend is called, so nothing was billed. A
    :class:`ValueError` as well, so code that caught one keeps working.
    :attr:`field` names the option and :attr:`allowed` its values, when it has
    a fixed set.
    """

    def __init__(self, field: str, reason: str, *, allowed: Sequence[Any] | None = None) -> None:
        self.field = field
        self.reason = reason
        self.allowed = list(allowed) if allowed is not None else None
        super().__init__(MSG_INVALID_PARAMETER.format(field=field, reason=reason))


class AllProvidersFailedError(ProviderError):
    """Every backend ``auto`` tried failed: :attr:`failures` maps each one's name to its own error.

    A :class:`ProviderError`, so an ``except`` for that still catches it. Its
    :attr:`kind` is the members' shared kind when they agree (every key refused
    is ``auth``), else ``upstream``; it is :attr:`retryable` when any member's
    failure was.
    """

    def __init__(self, provider: str, message: str, failures: Mapping[str, WebRouterError]) -> None:
        kinds = {getattr(exc, "kind", None) for exc in failures.values()}
        shared = kinds.pop() if len(kinds) == 1 else None
        super().__init__(provider, message, kind=shared or ERROR_KIND_UPSTREAM)
        self.failures = dict(failures)
        self.status_code = self.status_code or shared_status(
            getattr(exc, "status_code", None) for exc in failures.values()
        )
        delays = [d for d in (getattr(exc, "retry_after", None) for exc in self.failures.values()) if d is not None]
        self.retry_after = max(delays) if delays else None
        self.retryable = any(getattr(exc, "retryable", False) for exc in self.failures.values())


class ExtractFailedError(ProviderError):
    """An extract read no page, and the URLs failed in calls or answers of their own: :attr:`url_failures` has each one.

    The same per-URL records a partly successful extract returns in
    ``ExtractResponse.failures``, each with its own ``kind`` and ``retryable``,
    so one URL blocked and another gone are told apart. A :class:`ProviderError`,
    so an ``except`` for that still catches it; its :attr:`kind` sums the URLs up.
    """

    def __init__(
        self, provider: str, message: str, failures: Sequence[ExtractFailure], *, original: BaseException | None = None
    ) -> None:
        super().__init__(provider, message, original=original, kind=ExtractFailure.batch_kind(failures))
        self.url_failures = list(failures)


def shared_status(statuses: Iterable[int | None]) -> int | None:
    """The one status every failure answered with, else ``None``: a summary names a status only when all agree."""
    found = set(statuses)
    return found.pop() if len(found) == 1 else None

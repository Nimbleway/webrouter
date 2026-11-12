"""Exceptions a caller can branch on by their fields: a refused option, and every backend of a chain failing.

Kept apart from :mod:`web_router.exceptions`, so that file stops growing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from web_router.constants_errors import MSG_INVALID_PARAMETER
from web_router.exceptions import ProviderError, WebRouterError


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
        super().__init__(provider, message, kind=shared or "upstream")
        self.failures = dict(failures)
        self.retryable = any(getattr(exc, "retryable", False) for exc in self.failures.values())

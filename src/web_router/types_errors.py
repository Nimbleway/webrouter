"""What kind of failure a backend's error is, and a URL an extract could not read, so callers branch on fields.

A leaf of the import graph, like the other ``types_*`` modules: it imports nothing of web-router's, so
:mod:`web_router.types` and :mod:`web_router.exceptions` can both build on it. Import its names from there.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, model_validator

ProviderErrorKind = Literal["auth", "rate_limit", "bad_request", "not_found", "timeout", "target", "local", "upstream"]
"""Why a backend call failed.

- ``auth``: the credential was refused (401, 403).
- ``rate_limit``: throttled or out of quota (429, 402).
- ``bad_request``: the backend refused the request as sent (400, 422).
- ``not_found``: the backend's endpoint or resource is not there (404).
- ``timeout``: no answer in time.
- ``target``: the page asked for is the problem -- its host does not resolve, or its URL is not one -- so
  another backend would fail the same way.
- ``local``: a failure on this side of the call: a closed event loop, or a provider's own parsing bug.
- ``upstream``: anything else the backend did: a 5xx, a dropped connection, an answer that could not be read.
"""

RETRYABLE_KINDS: Final = frozenset({"rate_limit", "timeout", "upstream"})
"""The kinds a later retry, of the same backend or another, may get past."""

CHAIN_STOPPING_KINDS: Final = frozenset({"local", "target"})
RETRY_AFTER_HEADER: Final = "retry-after"
"""The kinds after which ``auto`` raises instead of trying its next backend: the next would fail the same way."""

KIND_BY_STATUS: Final[dict[int, ProviderErrorKind]] = {
    400: "bad_request",
    401: "auth",
    402: "rate_limit",
    403: "auth",
    404: "not_found",
    408: "timeout",
    422: "bad_request",
    429: "rate_limit",
}
"""An HTTP status's kind; any other 4xx is ``bad_request`` and any 5xx ``upstream``."""

TARGET_MARKERS: Final = (
    "nx_domain",
    "nxdomain",
    "name or service not known",
    "could not resolve host",
    "enotfound",
    "invalid url",
    "could not parse the provided url",
    "url must have a valid top-level domain",
)
"""Phrases, lowercased, by which a backend says the page's own address is the problem."""

LOCAL_LOOP_MARKERS: Final = ("event loop is closed", "attached to a different loop", "different event loop")
"""Phrases of a ``RuntimeError`` that come from reusing an async client across event loops."""

LOCAL_ERROR_TYPES: Final = (KeyError, TypeError, AttributeError, NameError, AssertionError, IndexError)
"""Exceptions a provider's own code raises on a bug, not ones a backend's answer causes."""

TARGET_GONE_STATUSES: Final = frozenset({404, 410})
"""A page's own status that says it is not there, whichever backend fetched it."""

ERROR_KIND_AUTH: Final[ProviderErrorKind] = "auth"
ERROR_KIND_LOCAL: Final[ProviderErrorKind] = "local"
ERROR_KIND_TARGET: Final[ProviderErrorKind] = "target"
ERROR_KIND_UPSTREAM: Final[ProviderErrorKind] = "upstream"
ERROR_KIND_TIMEOUT: Final[ProviderErrorKind] = "timeout"


@dataclass(frozen=True)
class ErrorFacts:
    """What a failure was: its kind, the HTTP status when there was one, and when to try again."""

    kind: ProviderErrorKind
    status_code: int | None = None
    retry_after: float | None = None

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


def classify(original: BaseException | None, message: str = "") -> ErrorFacts:
    """The facts of one failure, read from ``original`` first and from ``message`` where it says more.

    The status wins over the text, except that a backend naming the page's own
    address as the problem (an unresolvable host, an invalid URL) is ``target``
    whatever status it came with: no other backend could do better.
    """
    inner = getattr(original, "kind", None)
    if inner in get_args(ProviderErrorKind):
        # A backend's failure wrapped once more (a retry, a job poll) is still that failure.
        return ErrorFacts(
            cast(ProviderErrorKind, inner),
            getattr(original, "status_code", None),
            getattr(original, "retry_after", None),
        )
    status = _status(original)
    text = f"{message} {original if original is not None else ''}".lower()
    retry_after = _retry_after(original)
    # A failure to reach the backend itself says nothing about the page, whatever its text.
    if not _connect_failed(original) and any(marker in text for marker in TARGET_MARKERS):
        return ErrorFacts(ERROR_KIND_TARGET, status, retry_after)
    if status is not None:
        kind = KIND_BY_STATUS.get(status) or ("bad_request" if status < 500 else ERROR_KIND_UPSTREAM)
        return ErrorFacts(kind, status, retry_after)
    if _timed_out(original):
        return ErrorFacts(ERROR_KIND_TIMEOUT)
    if is_local(original):
        return ErrorFacts(ERROR_KIND_LOCAL)
    return ErrorFacts(ERROR_KIND_UPSTREAM)


def reused_loop(exc: BaseException | None) -> bool:
    """Whether ``exc`` is an async client used from an event loop other than the one it was built on."""
    return isinstance(exc, RuntimeError) and any(marker in str(exc).lower() for marker in LOCAL_LOOP_MARKERS)


def is_local(exc: BaseException | None) -> bool:
    """Whether ``exc`` came from this side of the call: a provider's own bug, or a client from a closed loop."""
    if isinstance(exc, RuntimeError):
        return any(marker in str(exc).lower() for marker in LOCAL_LOOP_MARKERS)
    return isinstance(exc, LOCAL_ERROR_TYPES)


def _status(original: BaseException | None) -> int | None:
    for source in (getattr(original, "response", None), original):
        for attribute in ("status_code", "status"):
            value = getattr(source, attribute, None)
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value < 600:
                return value
    return None


def _connect_failed(original: BaseException | None) -> bool:
    return original is not None and "connect" in type(original).__name__.lower()


def _timed_out(original: BaseException | None) -> bool:
    if isinstance(original, (TimeoutError, asyncio.TimeoutError)):
        return True
    return original is not None and "timeout" in type(original).__name__.lower()


def _retry_after(original: BaseException | None) -> float | None:
    """Seconds from a ``Retry-After`` header, as seconds or an HTTP date; ``None`` when absent or unreadable."""
    headers = getattr(getattr(original, "response", None), "headers", None)
    raw = headers.get(RETRY_AFTER_HEADER) if headers is not None else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        return max(0.0, seconds) if math.isfinite(seconds) else None
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class ExtractFailure(BaseModel):
    """A URL the provider could not fetch, and why.

    :attr:`kind` and :attr:`status_code` say what kind of failure it was, as
    on a :class:`~web_router.ProviderError`: a backend that knew sets them, and
    otherwise ``kind`` is read from :attr:`error`'s text.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    error: str
    kind: ProviderErrorKind | None = None
    status_code: int | None = None

    @model_validator(mode="after")
    def _kind_from_error(self) -> ExtractFailure:
        if self.kind is None:
            self.kind = classify(None, self.error).kind
        return self

    @staticmethod
    def shared_kind(failures: Sequence[ExtractFailure]) -> ProviderErrorKind | None:
        """``target`` when every URL failed for its own page, so a call that read none of them stops ``auto``."""
        if failures and all(failure.kind == ERROR_KIND_TARGET for failure in failures):
            return ERROR_KIND_TARGET
        return None

    @classmethod
    def from_error(cls, url: str, error: BaseException) -> ExtractFailure:
        """``url``'s failure from the error its call raised, keeping that error's kind and status."""
        return cls(
            url=url,
            error=str(error),
            kind=getattr(error, "kind", None),
            status_code=getattr(error, "status_code", None),
        )

    @property
    def retryable(self) -> bool:
        """Whether another try, on this backend or another, may get the page."""
        return self.kind in RETRYABLE_KINDS

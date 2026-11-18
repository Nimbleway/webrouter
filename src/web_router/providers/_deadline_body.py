"""A sync response body read under the deadline in force: the whole read is bounded, not each chunk of it.

httpx bounds every read of a socket by the request's read timeout, which the
transport clamps to the time left. A body that keeps arriving -- ten bytes
every half second -- never trips it, so a sync call could run minutes past its
deadline. The async path is cancelled at the deadline whatever the body does;
this holds the sync one to the same instant, between chunks.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from web_router.constants_production import MSG_BODY_PAST_DEADLINE
from web_router.providers._deadline import Deadline, current_deadline


class _BoundedBody(httpx.SyncByteStream):
    """``inner``, raising :class:`httpx.ReadTimeout` for a chunk that arrives after ``deadline``."""

    def __init__(self, inner: httpx.SyncByteStream, deadline: Deadline, request: httpx.Request) -> None:
        self._inner = inner
        self._deadline = deadline
        self._request = request

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._inner:
            if self._deadline.left() <= 0:
                raise httpx.ReadTimeout(MSG_BODY_PAST_DEADLINE, request=self._request)
            yield chunk

    def close(self) -> None:
        self._inner.close()


def bound_body(response: httpx.Response) -> None:
    """A sync client's response hook: read the body only until the deadline in force, when there is one.

    Raising a read timeout, as a stalled read would, is what makes the
    provider's deadline handling report it as
    :class:`~web_router.exceptions.DeadlineExceededError`.
    """
    deadline = current_deadline()
    if deadline is not None and isinstance(response.stream, httpx.SyncByteStream):
        response.stream = _BoundedBody(response.stream, deadline, response.request)

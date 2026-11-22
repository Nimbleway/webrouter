"""The sync deadline bounds the whole response, not each read: a body that trickles in is cut off at the deadline."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from tests.deadline_helpers import DEADLINE, _Acme, _call, _Clock
from web_router.exceptions import DeadlineExceededError
from web_router.providers._deadline import call_deadline
from web_router.types import SearchRequest

DRIP = 0.5
"""Seconds between chunks: each read is quick, so httpx's per-read timeout never fires."""


class _Trickle(httpx.SyncByteStream):
    """Ten bytes every :data:`DRIP` seconds, for minutes: a body that never stalls long enough to time out a read."""

    def __init__(self, clock: _Clock, chunks: list[bytes] | None = None) -> None:
        self._clock = clock
        self._chunks = chunks if chunks is not None else [b"0123456789"] * 400
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            self._clock.now += DRIP
            self.sent += 1
            yield chunk


def _trickling(body: _Trickle) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, stream=body))


@pytest.mark.parametrize("method", ["search", "extract", "answer"])
def test_a_trickling_body_stops_at_the_deadline_as_a_deadline(clock: _Clock, method: str) -> None:
    body = _Trickle(clock)

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError, match="3s deadline") as caught:
        _call(method, _trickling(body))

    assert clock.now <= DEADLINE + DRIP, f"read on to {clock.now}s, past the {DEADLINE}s deadline"
    assert body.sent < 400, "the body was read to the end"
    assert caught.value.provider == "acme"


def test_without_a_deadline_the_body_is_read_whole_and_the_callers_hooks_still_run(clock: _Clock) -> None:
    body = _Trickle(clock, [b"{", b"}"])
    seen: list[int] = []

    def keep(response: httpx.Response) -> None:
        seen.append(response.status_code)

    _Acme(transport=_trickling(body), event_hooks={"response": [keep]}).search(SearchRequest(query="q"))

    assert body.sent == 2, "no deadline, nothing cut"
    assert seen == [200]

"""What happens to the losers on the sync router: ``close()`` waits for every one of them."""

from __future__ import annotations

import threading
import time
from typing import Any

from tests.max_extract.conftest import REQUEST, _member, _race, _Recorder
from web_router.registry import register_extract_provider
from web_router.types import ExtractRequest, ExtractResponse


def test_close_waits_for_a_loser_still_running_rather_than_closing_under_it(members: _Recorder) -> None:
    _member(members, "slow", delay=0.3)
    _member(members, "fast")

    provider = _race("slow", "fast")
    response = provider.extract(REQUEST)
    assert response.provider == "fast"
    assert "slow" not in members.finished, "the loser is still running when the winner is returned"

    provider.close()

    assert "slow" in members.finished, "close() waited for it"


def test_close_waits_for_a_race_still_running_on_another_thread(members: _Recorder) -> None:
    """Not a snapshot of losers: a call in flight when close() starts is waited for too."""
    _member(members, "slow", delay=0.3)
    provider = _race("slow")
    outcome: list[ExtractResponse] = []

    racer = threading.Thread(target=lambda: outcome.append(provider.extract(REQUEST)))
    racer.start()
    time.sleep(0.05)  # the race has submitted its call and is waiting on it
    provider.close()

    assert "slow" in members.finished, "close() returned only once the live call had finished"
    racer.join()
    assert outcome[0].provider == "slow"


def test_close_waits_for_a_race_that_has_not_submitted_its_first_call_yet(members: _Recorder) -> None:
    """A race still building its members has nothing in flight; close() must wait for it all the same."""
    closed_before_use: list[bool] = []

    class Slow(_member(members, "slow-to-build")):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            time.sleep(0.2)
            super().__init__(**kwargs)

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            closed_before_use.append(self.closed)
            return super()._extract(request, urls)

    register_extract_provider("slow-to-build", Slow)
    provider = _race("slow-to-build")
    outcome: list[ExtractResponse] = []
    racer = threading.Thread(target=lambda: outcome.append(provider.extract(REQUEST)))
    racer.start()
    time.sleep(0.05)  # the race is inside the member's constructor: active, nothing in flight

    provider.close()
    racer.join()

    assert outcome[0].provider == "slow-to-build"
    assert closed_before_use == [False], "the member was used before close() closed it"


def test_many_quick_races_never_hang_on_a_call_that_finished_before_it_was_registered(members: _Recorder) -> None:
    """A done callback registered on a finished future runs inline; it must not need a lock the caller holds."""
    _member(members, "instant")
    provider = _race("instant")
    done = threading.Event()

    def run() -> None:
        for _ in range(50):
            provider.extract(REQUEST)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=10), "a race deadlocked"
    provider.close()

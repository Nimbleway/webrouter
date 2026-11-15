"""Cooldowns for an extract whose URLs fail in separate calls, and for overlapping throttles."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pytest

from tests.conftest import FakeExtractProvider
from tests.test_cooldown import _throttle
from web_router import WebRouter
from web_router.exceptions import ProviderError
from web_router.providers._cooldown import Cooldowns
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractRequest, ExtractResponse


class _Mixed(FakeExtractProvider):
    """One URL per call; each URL fails with the status ``statuses`` gives it."""

    name = "mixed"
    calls = 0
    statuses: ClassVar[dict[str, int]] = {}

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        type(self).calls += 1
        status = self.statuses.get(urls[0])
        if status is None:
            return self._respond(request, urls)
        raise _throttle(status) if status in (402, 429) else ProviderError("mixed", f"HTTP {status}")


@pytest.fixture(autouse=True)
def _members() -> Any:
    register_extract_provider("mixed", _Mixed)
    register_extract_provider("pages", FakeExtractProvider)
    yield
    unregister_extract_provider("mixed")
    unregister_extract_provider("pages")


@pytest.mark.parametrize(
    ("first", "second", "calls"),
    [(500, 402, 2), (402, 500, 2), (500, 429, 2), (402, 402, 2), (429, 429, 2), (500, 500, 3)],
)
def test_a_throttle_on_any_url_cools_the_backend_when_every_url_fails(first: int, second: int, calls: int) -> None:
    _Mixed.calls, _Mixed.statuses = 0, {"https://a.example/": first, "https://b.example/": second}
    with WebRouter(provider_config={"auto": {"chain": ["mixed", "pages"]}}) as router:
        router.extract(["https://a.example/", "https://b.example/"])
        router.extract("https://c.example/")
    assert _Mixed.calls == calls


def test_a_shorter_throttle_does_not_shorten_a_running_cooldown(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="web_router")
    cooldowns = Cooldowns()
    cooldowns.note("a", _throttle(429, "30"))
    cooldowns.note("a", _throttle(429, "5"))
    left = cooldowns.remaining("a")
    assert left is not None and left[0] > 25
    assert "cools down for 5.0s" not in caplog.text, "the log names the cooldown still running"

"""A router's cooldowns: after a 429 or 402, ``auto`` leaves that backend alone for its ``Retry-After``."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.conftest import FakeProvider
from web_router import AllProvidersFailedError, AsyncWebRouter, ProviderError, WebRouter
from web_router.providers import _cooldown
from web_router.providers._cooldown import Cooldowns
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse


def _throttle(status: int, retry_after: str | None = None) -> ProviderError:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://api.example/search"))
    original = httpx.HTTPStatusError("throttled", request=response.request, response=response)
    return ProviderError("throttled", f"HTTP {status}", original=original)


class _Throttled(FakeProvider):
    name = "throttled"
    calls = 0
    status = 429
    retry_after: str | None = "30"

    def _search(self, request: SearchRequest) -> SearchResponse:
        type(self).calls += 1
        raise _throttle(self.status, self.retry_after)

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        return self._search(request)


class _Backup(FakeProvider):
    name = "backup"


@pytest.fixture(autouse=True)
def _members() -> Any:
    _Throttled.calls, _Throttled.status, _Throttled.retry_after = 0, 429, "30"
    register_provider("throttled", _Throttled)
    register_provider("backup", _Backup)
    yield
    unregister_provider("throttled")
    unregister_provider("backup")


def _router(cls: type = WebRouter, chain: tuple[str, ...] = ("throttled", "backup")) -> Any:
    return cls(provider_config={"auto": {"chain": list(chain)}})


def test_a_429_with_retry_after_cools_the_backend_for_that_long(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(_cooldown.time, "monotonic", lambda: now[0])
    cooldowns = Cooldowns()
    cooldowns.note("tavily", _throttle(429, "12"))
    assert cooldowns.remaining("tavily") == (12.0, 429)
    now[0] += 12.5
    assert cooldowns.remaining("tavily") is None


@pytest.mark.parametrize(("status", "seconds"), [(429, 10.0), (402, 60.0)])
def test_a_throttle_without_retry_after_cools_for_the_statuss_default(status: int, seconds: float) -> None:
    cooldowns = Cooldowns()
    cooldowns.note("you-mcp", _throttle(status))
    left = cooldowns.remaining("you-mcp")
    assert left is not None and left[1] == status and seconds - 1 < left[0] <= seconds


def test_a_retry_after_past_the_cap_is_held_to_it_and_other_failures_set_none() -> None:
    cooldowns = Cooldowns()
    cooldowns.note("a", _throttle(429, "86400"))
    cooldowns.note("b", _throttle(503))
    cooldowns.note("c", ValueError("not a provider error"))
    left = cooldowns.remaining("a")
    assert left is not None and left[0] <= 600.0
    assert cooldowns.remaining("b") is None and cooldowns.remaining("c") is None


def test_auto_passes_a_cooling_backend_over_without_calling_it() -> None:
    with _router() as router:
        first = router.search("q")
        second = router.search("q", timeout=30)  # a budget's record, to read the hop from
    assert first.provider == second.provider == "backup"
    assert _Throttled.calls == 1, "the second call never reached the throttled backend"
    hop = second.decision.hops[0]
    assert hop.id == "search:throttled" and hop.error == "429"


async def test_the_async_router_cools_down_the_same_way() -> None:
    async with _router(AsyncWebRouter) as router:
        await router.search("q")
        await router.search("q")
    assert _Throttled.calls == 1


def test_every_backend_cooling_fails_at_once_naming_the_time_left() -> None:
    with _router(chain=("throttled",)) as router:
        with pytest.raises(AllProvidersFailedError):
            router.search("q")
        with pytest.raises(AllProvidersFailedError) as caught:
            router.search("q")
    assert _Throttled.calls == 1
    cooling = caught.value.failures["throttled"]
    assert cooling.kind == "rate_limit" and cooling.status_code == 429
    assert cooling.retry_after is not None and 0 < cooling.retry_after <= 30
    assert "cooling down" in str(cooling)


def test_a_backend_the_caller_names_is_still_called() -> None:
    with WebRouter() as router:
        for _ in range(2):
            with pytest.raises(ProviderError):
                router.search("q", provider="throttled")
    assert _Throttled.calls == 2


def test_each_router_keeps_its_own_cooldowns() -> None:
    with _router() as one, _router() as two:
        one.search("q")
        two.search("q")
    assert _Throttled.calls == 2

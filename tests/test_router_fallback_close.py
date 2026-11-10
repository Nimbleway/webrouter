"""Who closes a ``fallback`` router's members: one it built by name is closed with it; one passed in never is."""

from __future__ import annotations

from typing import Any

import pytest

from tests.routing_registry_helpers import NAME, Closable, built_fixture  # noqa: F401  (fixture)
from web_router import AsyncWebRouter, WebRouter, register_router, unregister_router
from web_router.exceptions import ConfigurationError
from web_router.routing.fallback import FallbackRouter


def test_closing_the_routers_closes_members_built_by_name_only(built: list[Closable]) -> None:
    mine = Closable()
    WebRouter(router="fallback", router_config={"routers": [NAME, mine]}).close()
    assert (built[0].closed, built[0].aclosed) == (1, 0)
    assert (mine.closed, mine.aclosed) == (0, 0)


async def test_acloseing_the_routers_acloses_members_built_by_name_only(built: list[Closable]) -> None:
    mine = Closable()
    await AsyncWebRouter(router="fallback", router_config={"routers": [mine, {"name": NAME}]}).aclose()
    assert (built[0].closed, built[0].aclosed) == (0, 1)
    assert (mine.closed, mine.aclosed) == (0, 0)


def test_a_member_refused_at_build_closes_those_built_before_it(built: list[Closable]) -> None:
    with pytest.raises(ConfigurationError):
        FallbackRouter(routers=[NAME, {"name": NAME, "unknown_key": 1}, object()])
    assert [router.closed for router in built] == [1, 1], "both built by name, then the third refused"


def test_a_fallback_refused_when_attached_closes_its_members(built: list[Closable]) -> None:
    with pytest.raises(ConfigurationError, match="timeout_s"):
        WebRouter(router="fallback", router_config={"routers": [NAME], "timeout_s": -1})
    assert built[0].closed == 1


class Failing(Closable):
    def close(self) -> None:
        super().close()
        raise RuntimeError("close failed")


def test_every_member_is_closed_before_a_failed_close_is_raised(built: list[Closable]) -> None:
    register_router("test-failing-close", Failing)
    try:
        router = FallbackRouter(routers=["test-failing-close", NAME])
    finally:
        unregister_router("test-failing-close")
    with pytest.raises(RuntimeError, match="close failed"):
        router.close()
    assert built[0].closed == 1


async def test_every_member_is_aclosed_before_a_failed_aclose_is_raised(built: list[Closable]) -> None:
    async def failing_aclose(self: Any) -> None:
        raise RuntimeError("aclose failed")

    register_router("test-failing-aclose", type("FailingA", (Closable,), {"aclose": failing_aclose}))
    try:
        router = FallbackRouter(routers=["test-failing-aclose", NAME])
    finally:
        unregister_router("test-failing-aclose")
    with pytest.raises(RuntimeError, match="aclose failed"):
        await router.aclose()
    assert built[0].aclosed == 1


class PolicyRaises(Closable):
    @property
    def min_url_policy(self) -> str:
        raise RuntimeError("policy unreadable")


def test_a_member_whose_url_policy_raises_is_closed_too(built: list[Closable]) -> None:
    made: list[PolicyRaises] = []
    register_router("test-policy-raises", lambda: made.append(PolicyRaises()) or made[-1])
    try:
        with pytest.raises(RuntimeError, match="policy unreadable"):
            FallbackRouter(routers=[NAME, "test-policy-raises"])
    finally:
        unregister_router("test-policy-raises")
    assert built[0].closed == 1 and made[0].closed == 1


class BadPolicy(Closable):
    min_url_policy = 3


def test_a_member_with_an_unknown_url_policy_is_refused_and_closed(built: list[Closable]) -> None:
    made: list[BadPolicy] = []
    register_router("test-bad-policy", lambda: made.append(BadPolicy()) or made[-1])
    try:
        with pytest.raises(ConfigurationError, match="url_policy"):
            FallbackRouter(routers=[NAME, "test-bad-policy"])
    finally:
        unregister_router("test-bad-policy")
    assert built[0].closed == 1 and made[0].closed == 1

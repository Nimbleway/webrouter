"""Routers by name: register_router / unregister_router, router_config and the record's name."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.routing_registry_helpers import NAME, Closable, registered
from web_router import AsyncWebRouter, RouteDecision, RouteRequest, WebRouter, register_router, unregister_router
from web_router.exceptions import ConfigurationError
from web_router.routing.registry import available_routers


@pytest.fixture
def built() -> Iterator[list[Closable]]:
    """NAME registered for the test; every router the factory builds is appended here."""
    with registered() as made:
        yield made


def test_a_registered_name_builds_its_router_with_router_config(built: list[Closable]) -> None:
    with WebRouter(router=NAME, router_config={"threshold": 0.5}) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert [router.config for router in built] == [{"threshold": 0.5}]
    assert plan.decision.reason == "registered"


def test_a_registered_name_is_listed_and_unregistering_it_refuses_it_again(built: list[Closable]) -> None:
    assert NAME in available_routers() and "remote" in available_routers()
    unregister_router(NAME)
    assert NAME not in available_routers()
    with pytest.raises(ConfigurationError, match="names no registered router"):
        WebRouter(router=NAME)
    unregister_router(NAME)  # a no-op the second time


def test_registering_remote_overrides_the_built_in_until_unregistered() -> None:
    register_router("remote", Closable)
    try:
        assert isinstance(WebRouter(router="remote")._owned_router, Closable)
    finally:
        unregister_router("remote")
    with pytest.raises(ConfigurationError, match="needs router_config"):
        WebRouter(router="remote")


def test_a_factory_that_returns_none_is_refused_by_name() -> None:
    register_router(NAME, lambda **config: None)  # type: ignore[arg-type]
    try:
        for cls in (WebRouter, AsyncWebRouter):
            with pytest.raises(ConfigurationError, match=f"router='{NAME}'.*returned None"):
                cls(router=NAME)
    finally:
        unregister_router(NAME)


@pytest.mark.parametrize("name", ["", "   ", 7])
def test_a_name_must_be_a_non_empty_string(name: Any) -> None:
    with pytest.raises(ConfigurationError, match="non-empty string"):
        register_router(name, Closable)


def test_a_factory_must_be_callable() -> None:
    with pytest.raises(ConfigurationError, match="takes a callable"):
        register_router(NAME, "not a factory")  # type: ignore[arg-type]


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
def test_router_config_without_a_name_is_refused(cls: type) -> None:
    with pytest.raises(ConfigurationError, match="router_config="):
        cls(router=Closable().route, router_config={"url": "https://example.com"})


def test_a_built_router_the_attach_refuses_is_closed_before_the_error() -> None:
    made: list[Any] = []

    class NotARouter:
        def close(self) -> None:
            made.append("closed")

    register_router(NAME, NotARouter)
    try:
        with pytest.raises(ConfigurationError, match="router="):
            WebRouter(router=NAME)
    finally:
        unregister_router(NAME)
    assert made == ["closed"]


# -- the record's name ----------------------------------------------------------------


def test_a_router_names_itself_in_the_record_with_router_name() -> None:
    class Named(Closable):
        router_name = "my-service(eu)"

    wr = WebRouter(router=Named())
    assert wr.plan("solar news").decision.router == "my-service(eu)"


def test_without_router_name_the_record_keeps_the_qualified_name() -> None:
    def ranker(req: RouteRequest) -> RouteDecision:
        return RouteDecision()

    assert WebRouter(router=ranker).plan("solar news").decision.router == ranker.__qualname__

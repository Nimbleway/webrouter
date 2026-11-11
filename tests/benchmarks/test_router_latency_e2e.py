"""router-latency, offline: the added wall time, on stubbed backends."""

from __future__ import annotations

from types import ModuleType

import pytest

from tests.benchmarks._router_latency_fakes import (
    latency_fixture,  # noqa: F401 -- the latency fixture
    module,
)
from web_router import RouteDecision, registry


def test_the_added_wall_time_runs_on_stubs_and_puts_the_registry_back(latency: ModuleType) -> None:
    run = module("router_latency_e2e")
    out = run.added_wall_time(
        {"none": lambda req: RouteDecision()}, ["q"] * 4, ("perplexity", "nimble"), warmup=1, seed=0, loop=None
    )
    assert out["none"]["calls"] == 3 and out["none"]["routed_p50_ms"] is not None
    assert "perplexity" not in registry._CUSTOM_PROVIDERS


def test_stubbing_puts_a_backend_of_your_own_back(latency: ModuleType) -> None:
    run = module("router_latency_e2e")
    mine = type("Mine", (run._Stub,), {"name": "mine"})
    registry.register_provider("perplexity", mine)
    try:
        with run.stubbed(("perplexity",)):
            assert registry._CUSTOM_PROVIDERS["perplexity"] is not mine
        assert registry._CUSTOM_PROVIDERS["perplexity"] is mine
    finally:
        registry.unregister_provider("perplexity")


def test_a_search_that_fails_mid_timing_still_closes_every_client(
    latency: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = module("router_latency_e2e")
    closed: list[str] = []

    class Client:
        def __init__(self, name: str) -> None:
            self.name = name

        def search(self, prompt: str) -> None:
            raise RuntimeError("backend down")

        def close(self) -> None:
            closed.append(self.name)

    monkeypatch.setattr(run, "_clients", lambda cls, router, chain: (Client("routed"), Client("unrouted")))
    with pytest.raises(RuntimeError, match="backend down"):
        run.added_wall_time({"a": None, "b": None}, ["q"] * 2, ("perplexity",), warmup=0, seed=0, loop=None)
    assert sorted(closed) == ["routed", "routed", "unrouted", "unrouted"]

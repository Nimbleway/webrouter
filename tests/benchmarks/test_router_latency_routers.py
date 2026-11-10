"""router-latency, offline: the yardstick and the rule, and the routers named on the command line, remote among them."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import ModuleType

import pytest

from tests.benchmarks._router_latency_fakes import (
    latency_fixture,  # noqa: F401 -- the latency fixture
    module,
    req,
)
from web_router import RouteDecision, RouteRequest
from web_router.routing.registry import discard_router


def test_a_tool_s_p50_is_the_first_backend_of_its_auto_chain(latency: ModuleType) -> None:
    tools = module("router_latency_tools").tool_latencies("all")
    assert tools["search"].head == "perplexity" and tools["search"].is_measured
    assert tools["search"].range_p50_s is not None and tools["search"].range_p50_s[0] <= tools["search"].p50_s
    assert tools["answer"].is_measured
    assert not any(tools[t].is_measured for t in ("extract", "research", "browse")), "never made up"


def test_the_rule_consults_under_a_quarter_and_never_weighs_what_is_not_measured(latency: ModuleType) -> None:
    mod = module("router_latency_tools")
    tool = mod.ToolLatency("search", "x", 1.0, 2.0, 10, None, None)
    assert mod.verdict(0.2, tool) == (mod.CONSULT, 0.2)
    assert mod.verdict(0.25, tool)[0] == mod.SKIP
    assert mod.verdict(None, tool) == (mod.UNSURE, None)
    assert mod.verdict(0.01, mod.ToolLatency("browse", "y", None, None, None, None, None)) == (mod.JUDGEMENT, None)


def test_remote_is_the_library_s_own_remote_router(latency: ModuleType) -> None:
    from web_router.routing.remote import RemoteRouter

    router = module("router_latency_routers").router_specs("remote:http://127.0.0.1:1/route")[0].build()
    try:
        assert isinstance(router, RemoteRouter)
    finally:
        discard_router(router)


def test_a_stated_rtt_adds_a_simulated_remote_beside_loopback(latency: ModuleType) -> None:
    specs = module("router_latency_routers").router_specs("remote:http://u:pw@127.0.0.1:1/r?token=s", rtts_ms=[40.0])
    assert [s.name for s in specs] == ["remote:http://127.0.0.1", "remote:http://127.0.0.1 +40ms"]
    assert "simulated 40 ms" in specs[1].describe
    assert not any(secret in s.name + s.describe for s in specs for secret in ("pw", "token", "/r"))


def test_a_simulated_link_keeps_the_call_within_its_deadline(latency: ModuleType) -> None:
    remote = module("router_latency_remote")
    seen: list[float] = []

    class Inner:
        timeout_s = 1.0

        async def aroute(self, req: RouteRequest) -> RouteDecision:
            seen.append(req.deadline_s)
            return RouteDecision()

    asyncio.run(remote.DelayedRouter(Inner(), 10.0).aroute(req()))  # first call: three round trips, 30 ms of 1 s
    assert seen and seen[0] == pytest.approx(0.97)
    with pytest.raises(TimeoutError):
        asyncio.run(remote.DelayedRouter(Inner(), 400.0).aroute(req()))
    link, short = remote.DelayedRouter(Inner(), 20.0), replace(req(), deadline_s=0.05)
    for _ in range(2):  # a handshake that timed out opened no connection, so the next call pays it again
        with pytest.raises(TimeoutError):
            asyncio.run(link.aroute(short))


def test_the_llm_estimate_counts_the_schema_only_for_a_preset_that_sends_it(latency: ModuleType) -> None:
    routers = module("router_latency_routers")
    strict, json_mode = routers.llm_tokens(req(), preset="openai"), routers.llm_tokens(req(), preset="deepseek")
    assert strict[0] > json_mode[0] and strict[1] == json_mode[1]
    own = {"base_url": "https://llm.example"}
    assert routers.llm_tokens(req(), **own, response_format="json_schema")[0] > routers.llm_tokens(req(), **own)[0]


def test_a_mistyped_llm_preset_is_refused_before_the_dry_run_plans_it(
    latency: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_ROUTER_PRESET", "opneai")
    with pytest.raises(SystemExit, match="LLM_ROUTER_PRESET='opneai'"):
        module("router_latency_routers").router_spec("llm", llm_model="m")

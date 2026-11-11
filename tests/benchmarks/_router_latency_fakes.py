"""What router-latency's tests share: the script loaded from its folder, its modules, a request and a router.

Never a paid call: the routers here answer at once. A test module imports ``latency_fixture`` to use ``latency``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from scripts.benchmark_loader import load_script
from web_router import RouteDecision, RouteRequest
from web_router.routing.contract import Candidate

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "router_latency.py"
SIBLINGS = (
    "_common",
    "_frozen_checks",
    "_frozen_mixed",
    "_tools_effort_plan",
    "frozen",
    "route_eval_paths",
    "route_eval_sets",
    "route_eval_rank",
    "route_eval_merge",
    "router_latency_tools",
    "router_latency_args",
    "router_latency_routers",
    "router_latency_remote",
    "router_latency_run",
    "router_latency_e2e",
    "router_latency_report",
)


@pytest.fixture(scope="module", name="latency")
def latency_fixture() -> Iterator[ModuleType]:
    with load_script(SCRIPT, "router_latency_under_test", SIBLINGS) as module:
        yield module


def module(name: str) -> ModuleType:
    return sys.modules[name]


def req(prompt: str = "who won") -> RouteRequest:
    candidates = (Candidate(tool="search", provider="perplexity"), Candidate(tool="search", provider="nimble"))
    return RouteRequest(prompt=prompt, candidates=candidates, deadline_s=1.0, pinned_tool="search")


class Closing:
    """A router that logs its name when closed."""

    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def route(self, req: RouteRequest) -> RouteDecision:
        return RouteDecision()

    def close(self) -> None:
        self.log.append(self.name)

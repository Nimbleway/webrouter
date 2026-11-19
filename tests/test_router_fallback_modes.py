"""Vendor modes through the ``fallback`` and ``remote`` routers: each member is shown modes only when it opts in.

A ``remote`` member opts in only through its ``router_config``; a request it sends carries at most 64 candidates.
Offline: the remote member talks to the reference server on a loopback port.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import Shown, clear_keys, set_keys
from tests.routing_remote_helpers import Recorder, config, running
from web_router import Candidate, RouteDecision, RouteRequest, WebRouter
from web_router.routing.fallback import FallbackRouter
from web_router.routing.wire import request_to_wire

CHAIN = {"auto": {"chain": {"search": ["exa", "tavily"]}}}
BACKENDS = ["search:exa", "search:tavily"]


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "exa", "tavily")


def empty(body: dict[str, Any]) -> dict[str, Any]:
    return {"schema": 1}  # decides nothing: the next member is asked


def planned(*members: Any) -> Any:
    with WebRouter(provider_config=CHAIN, router="fallback", router_config={"routers": list(members)}) as wr:
        return wr.plan("q", tool="search")


def sent(recorder: Recorder) -> list[str]:
    return [
        f"{c['tool']}:{c['provider']}" + (f":{c['mode']}" if "mode" in c else "")
        for c in recorder.bodies[-1]["candidates"]
    ]


def test_each_member_is_shown_modes_only_when_it_opts_in() -> None:
    plain, opted = Shown(), Shown(RouteDecision(providers={"search": ["tavily"]}), route_modes=True)
    recorder = Recorder(empty)
    with running(recorder) as server:
        plan = planned(plain, {"name": "remote", **config(server)}, opted)
    assert plain.ids == BACKENDS, "a local member that did not opt in"
    assert sent(recorder) == BACKENDS, "the remote member, not opted in"
    assert BACKENDS[0] in opted.ids and any(cid.startswith("search:exa:") for cid in opted.ids)
    assert plan.providers == {"search": ("tavily", "exa")}


def test_a_remote_member_opted_in_through_its_router_config_gets_modes() -> None:
    recorder = Recorder(empty)
    with running(recorder) as server:
        planned({"name": "remote", **config(server, route_modes=True)})
    assert BACKENDS[0] in sent(recorder) and any(cid.startswith("search:exa:") for cid in sent(recorder))


def test_the_fallback_opts_in_when_a_member_does_unless_set() -> None:
    assert FallbackRouter(routers=[Shown(), Shown(route_modes=True)]).route_modes is True
    assert FallbackRouter(routers=[Shown(), Shown()]).route_modes is False
    assert FallbackRouter(routers=[Shown(route_modes=True)], route_modes=False).route_modes is False


def _seventy() -> RouteRequest:
    """35 candidates for search and for answer: 5 backends each, every one with 6 modes after it."""
    candidates = [
        Candidate(tool=tool, provider=f"p{at}", mode=mode)
        for tool in ("search", "answer")
        for at in range(5)
        for mode in (None, *(f"m{n}" for n in range(6)))
    ]
    return RouteRequest(prompt="q", candidates=tuple(candidates), deadline_s=1.0)


def test_a_remote_request_carries_at_most_64_candidates_the_lowest_modes_dropped() -> None:
    req = _seventy()
    wire = request_to_wire(req)["candidates"]
    assert len(req.candidates) == 70 and len(wire) == 64
    kept = {(c["tool"], c["provider"], c.get("mode")) for c in wire}
    dropped = [(c.tool, c.provider, c.mode) for c in req.candidates if (c.tool, c.provider, c.mode) not in kept]
    # The last three places of each method's static order go first: p4's last three modes, in both methods.
    assert dropped == [(tool, "p4", f"m{n}") for tool in ("search", "answer") for n in (3, 4, 5)]
    assert [(c["tool"], c["provider"], c.get("mode")) for c in wire] == [
        (c.tool, c.provider, c.mode) for c in req.candidates if (c.tool, c.provider, c.mode) in kept
    ], "the rest keep their order"


def test_a_remote_router_attached_alone_is_sent_backends_only_whatever_the_caller_asks() -> None:
    recorder = Recorder(empty)
    with (
        running(recorder) as server,
        WebRouter(provider_config=CHAIN, router="remote", router_config=config(server), route_modes=True) as wr,
    ):
        wr.plan("q", tool="search")
    assert sent(recorder) == BACKENDS

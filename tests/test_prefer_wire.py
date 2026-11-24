"""``prefer`` and ``measured`` on the router wire, and the remote router that sends its own ``prefer``.

Each is sent only when it says something, so an unset request or decision is
the one a service got before either existed; a ``prefer`` a later version
adds reads as unset rather than refusing the call.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.decider_helpers import respond, serve
from web_router import Candidate, RouteDecision, RouteRequest
from web_router.exceptions import ConfigurationError
from web_router.routing.remote import RemoteRouter
from web_router.routing.wire import decision_from_wire, decision_to_wire, request_from_wire, request_to_wire

URL = "http://localhost:9000/route"
REQ = RouteRequest(prompt="q", candidates=(Candidate(tool="search", provider="exa"),), deadline_s=1)


def test_unset_the_request_and_decision_are_the_ones_sent_before() -> None:
    wire = request_to_wire(REQ)
    assert "prefer" not in wire and "measured" not in wire["candidates"][0]
    assert "prefer" not in decision_to_wire(RouteDecision(providers={"search": ["exa"]}))


def test_set_they_are_sent_and_read_back() -> None:
    req = replace(REQ, candidates=(Candidate(tool="search", provider="exa", measured=False),), prefer="accurate")
    wire = request_to_wire(req)
    assert (wire["prefer"], wire["candidates"][0]["measured"]) == ("accurate", False)
    assert request_from_wire(wire) == req
    decision = RouteDecision(providers={"search": ["exa"]}, prefer="fast")
    assert decision_from_wire(decision_to_wire(decision)) == decision


def test_a_preference_this_version_does_not_know_reads_as_unset() -> None:
    assert request_from_wire({**request_to_wire(REQ), "prefer": "balanced"}).prefer is None
    decision = decision_from_wire({"schema": 1, "providers": {"search": ["exa"]}, "prefer": "balanced"})
    assert decision == RouteDecision(providers={"search": ["exa"]})


async def test_the_remote_router_sends_its_preference_and_the_record_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, lambda _: respond({"schema": 1, "providers": {"search": ["exa"]}}))
    decision = await RemoteRouter(url=URL, prefer="accurate").aroute(REQ)
    assert server.body()["prefer"] == "accurate"
    assert decision.prefer == "accurate", "the service answered none of its own"


async def test_unset_the_remote_router_keeps_the_preference_it_was_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    server = serve(monkeypatch, lambda _: respond({"schema": 1, "providers": {"search": ["exa"]}, "prefer": "cheap"}))
    decision = await RemoteRouter(url=URL).aroute(replace(REQ, prefer="fast"))
    assert server.body()["prefer"] == "fast", "a router service passing it on never clears it"
    assert decision.prefer == "cheap", "the service's own answer wins"
    await RemoteRouter(url=URL).aroute(REQ)
    assert "prefer" not in server.body()


def test_the_remote_router_refuses_an_unknown_preference() -> None:
    with pytest.raises(ConfigurationError, match="prefer takes"):
        RemoteRouter(url=URL, prefer="cheapest")  # type: ignore[arg-type]

"""``prefer`` and a router: shown to it on every call and plan it is asked about, and recorded.

Each tool's ``auto`` runs over the routing harness's three fake members; a
router built with ``prefer`` is shown it in its :class:`RouteRequest`, a
remote one reads it in the wire format, and the decision record names it.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.plan_helpers import PROMPT, called_fixture, chooser  # noqa: F401  (fixture)
from tests.routing_helpers import Ranker, Tool, wr
from web_router import AsyncWebRouter, Candidate, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.fallback import FallbackRouter
from web_router.routing.wire import request_from_wire, request_to_wire


@pytest.mark.parametrize("prefer", ["cheapest", "CHEAP", "", 1, ["cheap"]])
def test_an_unknown_preference_is_refused_when_the_router_is_built(prefer: Any) -> None:
    with pytest.raises(ConfigurationError, match="prefer takes 'cheap', 'fast' or None"):
        wr(router=Ranker("third"), prefer=prefer)


def test_the_router_is_shown_the_preference_and_the_record_names_it(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    response = tool_call(wr(router=ranker, prefer="fast"), tool)
    assert [req.prefer for req in ranker.seen] == ["fast"]
    assert response.decision.prefer == "fast"


def test_with_no_preference_the_router_is_shown_none(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    response = tool_call(wr(router=ranker), tool)
    assert [req.prefer for req in ranker.seen] == [None]
    assert response.decision.prefer is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
async def test_the_async_router_shows_the_preference_too(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    response = await wr(AsyncWebRouter, router=ranker, prefer="cheap").search("who won")
    assert [req.prefer for req in ranker.seen] == ["cheap"]
    assert response.decision.prefer == "cheap"


@pytest.mark.usefixtures("called")
def test_a_plan_shows_the_router_the_preference_and_records_it() -> None:
    route = chooser("answer")
    plan = wr(router=route, prefer="cheap").plan(PROMPT)
    assert [req.prefer for req in route.seen] == ["cheap"]
    assert plan.decision.prefer == "cheap"


@pytest.mark.usefixtures("called")
def test_with_no_router_the_plan_still_records_the_preference() -> None:
    assert wr(router=None, prefer="cheap").plan(PROMPT).decision.prefer == "cheap"
    assert wr(router=None).plan(PROMPT).decision.prefer is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_fallback_router_passes_the_preference_to_its_members(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    wr(router=FallbackRouter(routers=[ranker]), prefer="fast").search("who won")
    assert [req.prefer for req in ranker.seen] == ["fast"]


def test_the_wire_carries_the_preference_and_an_old_request_reads_as_none() -> None:
    req = RouteRequest(prompt="q", candidates=(Candidate(tool="search", provider="exa"),), deadline_s=1, prefer="fast")
    wire = request_to_wire(req)
    assert wire["prefer"] == "fast"
    assert request_from_wire(wire) == req
    del wire["prefer"]  # a client from before prefer existed
    assert request_from_wire(wire).prefer is None
    unset = request_to_wire(RouteRequest(prompt="q", candidates=req.candidates, deadline_s=1))
    assert "prefer" not in unset, "unset, a service gets the request it got before prefer existed"


def tool_call(router: WebRouter, tool: Tool) -> Any:
    args = {"extract": (["https://a.test"],), "browse": ("book a table",)}.get(tool.name, ("who won",))
    return getattr(router, tool.name)(*args)

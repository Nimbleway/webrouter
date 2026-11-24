"""``prefer`` belongs to the router: the client takes none, and the record names the one a router ranked by.

Each tool's ``auto`` runs over the routing harness's three fake members. A
router's decision says what it preferred; the record copies it only when the
decision decided something with a value this version knows.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.plan_helpers import PROMPT, called_fixture, chooser  # noqa: F401  (fixture)
from tests.routing_helpers import Ranker, Tool, wr
from web_router import AsyncWebRouter, PreferredRouter, RouteDecision, RouteRequest, WebRouter
from web_router.routing.fallback import FallbackRouter


class Says:
    """A router that answers ``decision`` whatever it is shown."""

    def __init__(self, decision: RouteDecision) -> None:
        self.decision = decision

    def route(self, req: RouteRequest) -> RouteDecision:
        return self.decision


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
def test_the_client_takes_no_preference_of_its_own(cls: type) -> None:
    with pytest.raises(TypeError, match="prefer"):
        cls(prefer="cheap")


def test_the_record_names_the_preference_the_router_ranked_by(tool: Tool, called: list[str]) -> None:
    response = tool_call(wr(router=PreferredRouter("fast")), tool)
    assert response.decision.prefer == "fast"
    assert response.decision.router == "preferred"


def test_a_router_that_prefers_nothing_is_recorded_as_none(tool: Tool, called: list[str]) -> None:
    assert tool_call(wr(router=Ranker("third")), tool).decision.prefer is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize(
    "decision",
    [
        RouteDecision(prefer="cheap"),  # decides nothing: no preference was applied
        RouteDecision(providers={"search": ["third"]}, prefer="cheapest"),  # type: ignore[arg-type]
    ],
    ids=["decides-nothing", "unknown-value"],
)
def test_a_preference_that_ordered_nothing_or_is_unknown_is_not_recorded(
    tool: Tool, called: list[str], decision: RouteDecision
) -> None:
    assert tool_call(wr(router=Says(decision)), tool).decision.prefer is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_failed_router_records_no_preference(tool: Tool, called: list[str]) -> None:
    class Fails:
        def route(self, req: RouteRequest) -> RouteDecision:
            raise RuntimeError("down")

    assert tool_call(wr(router=Fails()), tool).decision.prefer is None


@pytest.mark.parametrize("tool", ["search"], indirect=True)
async def test_the_async_router_records_it_too(tool: Tool, called: list[str]) -> None:
    response = await wr(AsyncWebRouter, router=PreferredRouter("cheap")).search("who won")
    assert response.decision.prefer == "cheap"


@pytest.mark.usefixtures("called")
def test_a_plan_records_its_routers_preference() -> None:
    route = chooser("answer")
    assert wr(router=route).plan(PROMPT).decision.prefer is None
    assert (
        wr(router=Says(RouteDecision(tools=["answer"], prefer="accurate"))).plan(PROMPT).decision.prefer == "accurate"
    )


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_fallback_records_the_preference_of_the_member_that_counted(tool: Tool, called: list[str]) -> None:
    router = FallbackRouter(routers=[Says(RouteDecision()), PreferredRouter("fast")])
    assert tool_call(wr(router=router), tool).decision.prefer == "fast"


def tool_call(router: WebRouter, tool: Tool) -> Any:
    args = {"extract": (["https://a.test"],), "browse": ("book a table",)}.get(tool.name, ("who won",))
    return getattr(router, tool.name)(*args)

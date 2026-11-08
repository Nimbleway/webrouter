"""Attaching a router: the two forms it takes, what is refused, and the time it is given."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.invoke_helpers import DECISION, Both, async_router, make_req, sync_router
from web_router import AsyncWebRouter, Budget, RouteDecision, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.providers._deadline import call_deadline
from web_router.routing.invoke import AttachedRouter


class _OnlyRoute:
    def route(self, req: RouteRequest) -> RouteDecision:
        return DECISION


class _OnlyAroute:
    async def aroute(self, req: RouteRequest) -> RouteDecision:
        return DECISION


FORMS = [sync_router, async_router, _OnlyRoute(), _OnlyAroute(), Both()]
FORM_IDS = ["function", "coroutine-function", "route", "aroute", "both"]


@pytest.mark.parametrize("router", FORMS, ids=FORM_IDS)
def test_each_form_answers_from_sync_code(router: Any) -> None:
    outcome = AttachedRouter(router).call(make_req())
    assert outcome.decision is DECISION
    assert outcome.failure is None


@pytest.mark.parametrize("router", FORMS, ids=FORM_IDS)
async def test_each_form_answers_from_async_code(router: Any) -> None:
    outcome = await AttachedRouter(router).acall(make_req())
    assert outcome.decision is DECISION
    assert outcome.failure is None


def test_an_object_with_both_sides_is_called_on_the_matching_one() -> None:
    both = Both()
    attached = AttachedRouter(both)
    attached.call(make_req())
    asyncio.run(attached.acall(make_req()))
    assert both.sides == ["route", "aroute"]


@pytest.mark.parametrize(
    "router", ["acme", "remote", 42, None.__class__, _OnlyRoute], ids=["name", "remote", "int", "NoneType", "class"]
)
def test_anything_else_is_a_configuration_error(router: Any) -> None:
    with pytest.raises(ConfigurationError):
        AttachedRouter(router)


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
@pytest.mark.parametrize("name", ["acme", "remote"])
def test_a_router_given_by_name_is_refused_by_both_routers(cls: type, name: str) -> None:
    with pytest.raises(ConfigurationError, match="router="):
        cls(router=name)


def test_the_default_timeout_is_50_ms() -> None:
    assert AttachedRouter(sync_router).timeout_s == 0.05


def test_a_function_or_an_object_declares_its_own_timeout() -> None:
    def slow(req: RouteRequest) -> RouteDecision:
        return DECISION

    slow.timeout_s = 3  # type: ignore[attr-defined]
    obj = _OnlyRoute()
    obj.timeout_s = 1.5  # type: ignore[attr-defined]
    assert AttachedRouter(slow).timeout_s == 3.0
    assert AttachedRouter(obj).timeout_s == 1.5


@pytest.mark.parametrize("value", [0, -1, "3", True, float("nan"), float("inf")])
def test_a_timeout_that_is_not_a_positive_number_is_refused(value: Any) -> None:
    obj = _OnlyRoute()
    obj.timeout_s = value  # type: ignore[attr-defined]
    with pytest.raises(ConfigurationError, match="timeout_s"):
        AttachedRouter(obj)


def test_the_router_gets_the_least_of_its_timeout_the_budget_and_the_call_deadline() -> None:
    obj = _OnlyRoute()
    obj.timeout_s = 2.0  # type: ignore[attr-defined]
    attached = AttachedRouter(obj)
    assert attached.deadline(None) == 2.0
    assert attached.deadline(Budget(time_limit_s=0.5)) == 0.5
    with call_deadline(0.2):
        assert 0 < attached.deadline(Budget(time_limit_s=0.5)) <= 0.2

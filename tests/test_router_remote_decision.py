"""The decision a ``remote`` router service answers: another version, no schema, unknown fields, a long reason."""

from __future__ import annotations

from typing import Any

import pytest

from tests.routing_remote_helpers import URL_PROMPT, config, reference, running
from web_router import RouterError, WebRouter
from web_router.routing.remote import RemoteRouterError, RemoteRouterInvalid


def test_a_decision_of_another_schema_version_is_router_error() -> None:
    with running(lambda body: {"schema": 2, "tools": ["extract"]}) as server:
        plan = WebRouter(router="remote", router_config=config(server)).plan(URL_PROMPT)
        assert plan.tool == "search" and plan.decision.reason == "router_error"
        strict = WebRouter(router="remote", router_config=config(server), fail_on_router_error=True)
        with pytest.raises(RouterError, match="router_error") as raised:
            strict.plan(URL_PROMPT)
    cause = raised.value.__cause__
    assert isinstance(cause, RemoteRouterError) and not isinstance(cause, RemoteRouterInvalid)
    assert "version other than 1" in str(cause)


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ({"tools": ["extract"]}, "missing or not a number"),
        ({"schema": 1, "providers": {"search": [f"p{i}" for i in range(65)]}}, "more than 64 entries"),
        ({"schema": 1, "params": {"search": {f"o{i}": 1 for i in range(65)}}}, "64 entries"),
        ({"schema": 1, "params": {"extract": {"urls": [f"https://e{i}.example/" for i in range(65)]}}}, "64 entries"),
    ],
    ids=["no-schema", "65-providers", "65-options", "65-extract-urls"],
)
def test_a_decision_without_a_schema_or_of_another_shape_is_router_invalid(answer: dict[str, Any], why: str) -> None:
    with running(lambda body: answer) as server:
        wr = WebRouter(router="remote", router_config=config(server))
        plan = wr.plan(URL_PROMPT)
        assert plan.tool == "search" and plan.decision.reason == "router_invalid"
        strict = WebRouter(router="remote", router_config=config(server), fail_on_router_error=True)
        with pytest.raises(RouterError, match="router_invalid") as raised:
            strict.plan(URL_PROMPT)
    assert isinstance(raised.value.__cause__, RemoteRouterInvalid)
    assert why in str(raised.value.__cause__)


def test_unknown_decision_fields_are_ignored() -> None:
    def extended(body: dict[str, Any]) -> dict[str, Any]:
        return {**reference.decide(body), "model": "v7", "trace": {"id": "abc"}, "schema_minor": 3}

    with running(extended) as server:
        plan = WebRouter(router="remote", router_config=config(server)).plan(URL_PROMPT)
    assert plan.tool == "extract" and plan.decision.reason == "the prompt names a URL"


def test_a_long_reason_is_cut_short() -> None:
    with running(lambda body: {"schema": 1, "tools": ["search"], "reason": "x" * 5000}) as server:
        plan = WebRouter(router="remote", router_config=config(server)).plan("solar news")
    assert plan.decision.reason == "x" * 300

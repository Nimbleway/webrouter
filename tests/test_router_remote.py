"""The built-in ``remote`` router against the reference server: answers, timeouts, bad answers, unknown fields."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from tests.routing_remote_helpers import Recorder, config, reference, running
from web_router import AsyncWebRouter, Budget, RouterError, WebRouter
from web_router.routing.remote import RemoteRouter, RemoteRouterError
from web_router.routing.wire import request_from_wire

URL_PROMPT = "read https://en.wikipedia.org/wiki/Web_scraping"


def test_the_reference_server_routes_a_plan_with_the_rules_router() -> None:
    recorder = Recorder()
    with running(recorder) as server, WebRouter(router="remote", router_config=config(server)) as wr:
        plan = wr.plan(URL_PROMPT)
        searched = wr.plan("solar news")
    assert (plan.tool, searched.tool) == ("extract", "search")
    assert plan.decision.router == "remote(http://127.0.0.1)"
    assert plan.decision.reason == "the prompt names a URL"
    sent = recorder.bodies[0]
    assert sent["schema"] == 1 and sent["urls"] == ["https://en.wikipedia.org/wiki/Web_scraping"]
    assert all(c["id"] == f"{c['tool']}:{c['provider']}" for c in sent["candidates"])
    assert request_from_wire(sent).prompt == URL_PROMPT


async def test_the_async_router_asks_it_too() -> None:
    with running() as server:
        async with AsyncWebRouter(router="remote", router_config=config(server)) as wr:
            plans = [await wr.plan(URL_PROMPT), await wr.plan(URL_PROMPT)]
    assert [plan.tool for plan in plans] == ["extract", "extract"]
    assert plans[1].decision.router == "remote(http://127.0.0.1)"


def test_one_router_serves_several_event_loops() -> None:
    with running() as server:
        router = RemoteRouter(**config(server))
        req = request_from_wire(_sample())
        first = asyncio.run(router.aroute(req))
        second = asyncio.run(router.aroute(req))
        router.close()
    assert first.tools == second.tools == ["search"]


def _sample() -> dict[str, Any]:
    """A schema-1 request with one search candidate."""
    return {
        "schema": 1,
        "prompt": "solar news",
        "candidates": [{"tool": "search", "provider": "exa"}],
        "deadline_s": 1.0,
    }


def test_the_deadline_is_its_timeout_s_cut_to_the_budget() -> None:
    recorder = Recorder()
    with running(recorder) as server, WebRouter(router="remote", router_config=config(server, timeout_s=2.5)) as wr:
        wr.plan("solar news")
        wr.plan("solar news", budget=Budget(time_limit_s=0.7))
    assert [body["deadline_s"] for body in recorder.bodies] == [2.5, pytest.approx(0.7, abs=0.05)]
    assert recorder.bodies[1]["budget"]["time_limit_s"] == 0.7


def test_the_default_timeout_is_one_second() -> None:
    with running() as server:
        assert RemoteRouter(url=server.url).timeout_s == 1.0


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
async def test_a_slow_service_times_out_to_the_static_order(cls: type) -> None:
    def slow(body: dict[str, Any]) -> dict[str, Any]:
        time.sleep(1.0)
        return reference.decide(body)

    with running(slow) as server:
        wr = cls(router="remote", router_config=config(server, timeout_s=0.1))
        started = time.perf_counter()
        plan = wr.plan(URL_PROMPT)
        plan = await plan if cls is AsyncWebRouter else plan
        assert time.perf_counter() - started < 0.8
    assert plan.tool == "search", "the static order: no router, so a search"
    assert plan.decision.reason in {"router_timeout", "router_error"}


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ({"schema": 2, "tools": ["extract"]}, "wire schema 2"),
        ({"tools": ["extract"]}, "wire schema None"),
        ({"schema": 1, "tools": "extract"}, "does not fit"),
    ],
    ids=["schema-2", "no-schema", "ill-typed"],
)
def test_a_decision_of_another_schema_or_shape_is_a_router_error(answer: dict[str, Any], why: str) -> None:
    with running(lambda body: answer) as server:
        wr = WebRouter(router="remote", router_config=config(server))
        plan = wr.plan(URL_PROMPT)
        assert plan.tool == "search" and plan.decision.reason == "router_error"
        strict = WebRouter(router="remote", router_config=config(server), fail_on_router_error=True)
        with pytest.raises(RouterError) as raised:
            strict.plan(URL_PROMPT)
    assert isinstance(raised.value.__cause__, RemoteRouterError)
    assert why in str(raised.value.__cause__)


def test_unknown_decision_fields_are_ignored() -> None:
    def extended(body: dict[str, Any]) -> dict[str, Any]:
        return {**reference.decide(body), "model": "v7", "trace": {"id": "abc"}, "schema_minor": 3}

    with running(extended) as server:
        plan = WebRouter(router="remote", router_config=config(server)).plan(URL_PROMPT)
    assert plan.tool == "extract" and plan.decision.reason == "the prompt names a URL"


def test_an_error_status_or_a_body_that_is_not_json_is_a_router_error() -> None:
    with running() as server:
        missing = RemoteRouter(url=server.url.replace("/route", "/nowhere"), api_key="k")
        with pytest.raises(RemoteRouterError, match="HTTP 404"):
            missing.route(request_from_wire(_sample()))
    not_an_object: Any = lambda body: "not a decision"  # noqa: E731
    with running(not_an_object) as server, pytest.raises(RemoteRouterError, match="JSON object"):
        RemoteRouter(**config(server)).route(request_from_wire(_sample()))


def test_an_oversized_answer_is_a_router_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web_router.routing.remote.REMOTE_MAX_RESPONSE_BYTES", 64)
    with running(lambda body: {"schema": 1, "reason": "x" * 200}) as server:
        router = RemoteRouter(**config(server))
        with pytest.raises(RemoteRouterError, match="over 64 bytes"):
            router.route(request_from_wire(_sample()))
        with pytest.raises(RemoteRouterError, match="over 64 bytes"):
            asyncio.run(router.aroute(request_from_wire(_sample())))


def test_nothing_listening_is_a_router_error_without_a_retry() -> None:
    with running() as server:
        url = server.url
    router = RemoteRouter(url=url)
    req = request_from_wire({"schema": 1, "prompt": "x", "candidates": [], "deadline_s": 0.5})
    with pytest.raises(RemoteRouterError, match="no answer"):
        router.route(req)


def test_closing_hands_an_async_pool_to_the_loop_it_runs_on() -> None:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        with running() as server:
            router = RemoteRouter(**config(server))
            asyncio.run_coroutine_threadsafe(router.aroute(request_from_wire(_sample())), loop).result(5)
            pool = router._aclient
            assert pool is not None and not pool.is_closed
            router.close()
            deadline = time.monotonic() + 5
            while not pool.is_closed and time.monotonic() < deadline:
                time.sleep(0.01)
        assert pool.is_closed and router._aclient is None
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()

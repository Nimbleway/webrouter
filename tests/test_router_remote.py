"""The built-in ``remote`` router against the reference server: answers, timeouts and answers it cannot read."""

from __future__ import annotations

import asyncio
import gzip
import time
from concurrent.futures import Future
from typing import Any

import pytest

from tests.routing_remote_helpers import URL_PROMPT, Canned, Recorder, ask, canned, config, reference, running
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.routing._runner import _classified
from web_router.routing.remote import RemoteRouter, RemoteRouterError, RemoteRouterInvalid, RemoteRouterTimeout
from web_router.routing.wire import request_from_wire


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


async def test_the_async_router_asks_it_too_on_one_pool() -> None:
    with running() as server:
        async with AsyncWebRouter(router="remote", router_config=config(server)) as wr:
            plans = [await wr.plan(URL_PROMPT), await wr.plan(URL_PROMPT)]
            pool = wr._owned_router._pool  # type: ignore[union-attr]
        assert pool is not None and pool.is_closed, "aclose() closed the router's pool"
    assert [plan.tool for plan in plans] == ["extract", "extract"]
    assert plans[1].decision.router == "remote(http://127.0.0.1)"


def test_it_is_async_only_and_the_sync_router_closes_its_pool() -> None:
    with running() as server:
        wr = WebRouter(router="remote", router_config=config(server))
        remote = wr._owned_router
        assert not hasattr(remote, "route") and hasattr(remote, "aroute")
        wr.plan("solar news")
        pool = remote._pool  # type: ignore[union-attr]
        wr.close()
    assert pool is not None and pool.is_closed


def test_the_deadline_is_its_timeout_s_cut_to_the_budget() -> None:
    recorder = Recorder()
    with running(recorder) as server, WebRouter(router="remote", router_config=config(server, timeout_s=2.5)) as wr:
        wr.plan("solar news")
        wr.plan("solar news", budget=Budget(time_limit_s=0.7))
    assert [body["deadline_s"] for body in recorder.bodies] == [2.5, pytest.approx(0.7, abs=0.05)]
    assert recorder.bodies[1]["budget"]["time_limit_s"] == 0.7


def test_with_no_timeout_s_a_slow_service_is_abandoned_at_one_second() -> None:
    def slow(body: dict[str, Any]) -> dict[str, Any]:
        time.sleep(2.5)
        return reference.decide(body)

    recorder = Recorder(slow)
    with running(recorder) as server, WebRouter(router="remote", router_config=config(server)) as wr:
        started = time.perf_counter()
        plan = wr.plan(URL_PROMPT)
        waited = time.perf_counter() - started
    assert plan.decision.reason == "router_timeout"
    assert 0.9 < waited < 1.8, "the 1 s default, not the service's 2.5 s"
    assert recorder.bodies[0]["deadline_s"] == 1.0


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
    assert plan.decision.reason == "router_timeout"


def test_a_slow_drip_answer_is_cancelled_at_the_deadline() -> None:
    with canned(Canned(body=b'{"schema": 1, "tools": ["extract"]}', drip_s=0.05)) as base:
        wr = WebRouter(router="remote", router_config={"url": base + "/route", "timeout_s": 0.3})
        started = time.perf_counter()
        plan = wr.plan(URL_PROMPT)
        assert time.perf_counter() - started < 1.0, "each byte beats httpx's read timeout; the deadline does not"
    assert plan.decision.reason == "router_timeout"


def test_an_error_status_is_a_router_error_and_a_body_that_is_not_a_json_object_is_invalid() -> None:
    with running() as server:
        missing = RemoteRouter(url=server.url.replace("/route", "/nowhere"), api_key="k")
        with pytest.raises(RemoteRouterError, match="HTTP 404") as raised:
            ask(missing)
        assert not isinstance(raised.value, RemoteRouterInvalid)
    not_an_object: Any = lambda body: "not a decision"  # noqa: E731
    with running(not_an_object) as server, pytest.raises(RemoteRouterInvalid, match="JSON object"):
        ask(RemoteRouter(**config(server)))


@pytest.mark.parametrize("content_type", ["text/html", "text/plain; charset=utf-8", ""])
def test_an_answer_that_is_not_application_json_is_invalid(content_type: str) -> None:
    answer = Canned(body=b'{"schema": 1, "tools": ["extract"]}', headers={"Content-Type": content_type})
    with canned(answer) as base, pytest.raises(RemoteRouterInvalid, match="not application/json"):
        ask(RemoteRouter(url=base + "/route"))


def test_json_with_a_charset_is_read() -> None:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    with canned(Canned(body=b'{"schema": 1, "tools": ["extract"]}', headers=headers)) as base:
        assert ask(RemoteRouter(url=base + "/route")).tools == ["extract"]


def test_an_answer_over_64_kib_is_invalid() -> None:
    with running(lambda body: {"schema": 1, "reason": "x" * (64 * 1024)}) as server:
        with pytest.raises(RemoteRouterInvalid, match="over 65536 bytes"):
            ask(RemoteRouter(**config(server)))
        plan = WebRouter(router="remote", router_config=config(server)).plan("solar news")
    assert plan.decision.reason == "router_invalid"


@pytest.mark.parametrize("over", [0, 1], ids=["at-the-cap", "one-byte-over"])
def test_an_answer_of_exactly_64_kib_is_read_and_one_byte_more_is_invalid(over: int) -> None:
    head, tail = b'{"schema": 1, "reason": "', b'"}'
    body = head + b"x" * (64 * 1024 - len(head) - len(tail) + over) + tail
    with canned(Canned(body=body)) as base:
        if not over:
            assert ask(RemoteRouter(url=base + "/route")).reason is not None
            return
        with pytest.raises(RemoteRouterInvalid, match="over 65536 bytes"):
            ask(RemoteRouter(url=base + "/route"))


def test_a_compressed_answer_is_invalid_and_never_decompressed() -> None:
    bomb = gzip.compress(b"{" + b" " * (8 * 1024 * 1024) + b"}")
    answer = Canned(body=bomb, headers={"Content-Encoding": "gzip"})
    with canned(answer) as base, pytest.raises(RemoteRouterInvalid, match="Content-Encoding"):
        ask(RemoteRouter(url=base + "/route"))
    assert answer.seen[0][1]["Accept-Encoding"] == "identity"


def test_a_plain_answer_under_the_cap_is_read() -> None:
    answer = Canned(body=b'{"schema": 1, "tools": ["extract"]}', headers={"Content-Encoding": "identity"})
    with canned(answer) as base:
        assert ask(RemoteRouter(url=base + "/route")).tools == ["extract"]


def test_nothing_listening_is_a_router_error_without_a_retry() -> None:
    with running() as server:
        url = server.url
    with pytest.raises(RemoteRouterError, match="no answer"):
        ask(RemoteRouter(url=url))


def test_a_reply_nested_too_deep_to_parse_is_invalid() -> None:
    deep = b'{"schema": 1, "params": {"search": ' + b"[" * 30_000 + b"]" * 30_000 + b"}}"
    with canned(Canned(body=deep)) as base, pytest.raises(RemoteRouterInvalid, match="not a JSON object"):
        ask(RemoteRouter(url=base + "/route"))


def test_the_transport_giving_up_at_the_deadline_is_a_timeout_not_an_error() -> None:
    late = request_from_wire({"schema": 1, "prompt": "solar news", "candidates": [], "deadline_s": 0.2})
    with canned(Canned(body=b'{"schema": 1}', drip_s=1.0)) as base, pytest.raises(RemoteRouterTimeout, match="Timeout"):
        asyncio.run(RemoteRouter(url=base + "/route").aroute(late))
    done: Future[Any] = Future()
    done.set_exception(RemoteRouterTimeout("https://router.example.com", "late"))
    assert _classified(done)[0] == "router_timeout", "recorded as a timeout even when it beats the outer wait"

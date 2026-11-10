"""The ``remote`` router's key: sent only to the service, never in a record or failure, nor down a redirect or proxy."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.routing_remote_helpers import KEY, Canned, Recorder, canned, config, running
from web_router import RouterError, WebRouter
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY

PROVIDER_KEY = "fake-provider-secret-value-42"


def test_the_service_gets_its_bearer_key_and_a_wrong_one_is_turned_away(caplog: pytest.LogCaptureFixture) -> None:
    prompt = "read https://example.com/a"
    with running() as server:
        caplog.set_level(logging.DEBUG)
        good = WebRouter(router="remote", router_config=config(server)).plan(prompt)
        wrong = {**config(server), "api_key": "nope"}
        bad = WebRouter(router="remote", router_config=wrong).plan(prompt)
        with pytest.raises(RouterError) as raised:
            WebRouter(router="remote", router_config=wrong, fail_on_router_error=True).plan(prompt)
    assert good.tool == "extract" and bad.decision.reason == "router_error"
    assert "HTTP 401" in str(raised.value.__cause__)
    assert KEY not in caplog.text and "nope" not in caplog.text


def test_the_key_comes_from_the_environment_once_when_router_config_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_REMOTE_ROUTER_API_KEY, KEY)
    with running() as server:
        wr = WebRouter(router="remote", router_config={"url": server.url})
        monkeypatch.setenv(ENV_REMOTE_ROUTER_API_KEY, "changed-later")
        plan = wr.plan("read https://example.com/a")
    assert plan.tool == "extract", "the key read when the router was built is the one sent"


def test_no_key_value_reaches_the_service_the_record_or_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", PROVIDER_KEY)
    recorder = Recorder()
    with running(recorder) as server:
        wr = WebRouter(router="remote", router_config=config(server))
        plan = wr.plan("solar news")
        url = server.url
    record = plan.decision.model_dump_json()
    assert "search:tavily" in recorder.raw[0], "candidate ids are sent by design: they name configured backends"
    for text in (*recorder.raw, record, repr(plan), repr(wr._owned_router)):
        assert PROVIDER_KEY not in text and KEY not in text
    strict = WebRouter(router="remote", router_config={"url": url, "api_key": KEY}, fail_on_router_error=True)
    with pytest.raises(RouterError) as raised:
        strict.plan("solar news")  # nothing listens any more: a transport error
    cause = raised.value.__cause__
    assert "remote(http://127.0.0.1)" in str(raised.value) and "no answer" in str(cause)
    assert cause is not None and cause.__cause__ is None and cause.__context__ is None, "httpx's error names the URL"
    assert KEY not in str(raised.value) + str(cause)


def test_a_service_echoing_its_key_in_its_reasons_has_it_redacted() -> None:
    def echo(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": 1,
            "tools": ["search"],
            "reason": f"key {KEY}",
            "exclude": {"search:exa": KEY, KEY: "x"},
            "params": {"search": {KEY: 1}},
        }

    with running(echo) as server, WebRouter(router="remote", router_config=config(server)) as wr:
        plan = wr.plan("solar news")
    record = plan.decision.model_dump_json()
    assert KEY not in record and "[redacted]" in record


def test_an_escaped_echo_is_redacted_and_a_numeric_key_changes_no_number() -> None:
    escaped = KEY.replace("r", "\\u0072")  # the key, JSON-escaped: decoded, it is the key again
    body = f'{{"schema": 1, "tools": ["search"], "reason": "key {escaped}"}}'.encode()
    with canned(Canned(body=body)) as base:
        plan = WebRouter(router="remote", router_config={"url": base + "/route", "api_key": KEY}).plan("solar news")
    record = plan.decision.model_dump_json()
    assert KEY not in record and "[redacted]" in record
    with canned(Canned(body=b'{"schema": 1, "tools": ["search"]}')) as base:
        plan = WebRouter(router="remote", router_config={"url": base + "/route", "api_key": "1"}).plan("solar news")
    assert plan.decision.reason != "router_invalid", "a key of 1 leaves the schema version alone"
    with canned(Canned(body=b'{"schema": 1, "tools": ["search"]}')) as base:
        plan = WebRouter(router="remote", router_config={"url": base + "/route", "api_key": "schema"}).plan("x")
    assert plan.decision.reason != "router_invalid", "a key spelled like a field leaves the field names alone"


def test_a_redirect_is_a_router_error_and_the_key_never_follows_it() -> None:
    elsewhere = Canned(body=b'{"schema": 1, "tools": ["extract"]}')
    with canned(elsewhere) as target:
        redirect = Canned(status=307, body=b"", headers={"Location": target.replace("127.0.0.1", "localhost") + "/x"})
        with canned(redirect) as base:
            plan = WebRouter(router="remote", router_config={"url": base + "/route", "api_key": KEY}).plan("solar news")
    assert len(redirect.seen) == 1 and elsewhere.seen == [], "the redirect is not followed"
    assert plan.decision.reason == "router_error"


def test_an_environment_proxy_never_sees_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = Canned(body=b'{"schema": 1, "tools": ["research"]}')
    with canned(proxy) as proxy_url, running() as server:
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, proxy_url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        plan = WebRouter(router="remote", router_config=config(server)).plan("read https://example.com/a")
    assert proxy.seen == [] and plan.tool == "extract"

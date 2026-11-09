"""The ``remote`` router's guards: its URL, its key, redirects and proxies, the URLs it may set, what it is sent."""

from __future__ import annotations

import builtins
import logging
import socket
import time
from typing import Any

import pytest

from tests.routing_remote_helpers import KEY, Canned, Recorder, canned, config, reference, running
from web_router import RouteDecision, RouteRequest, RouterError, WebRouter
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY
from web_router.exceptions import ConfigurationError
from web_router.routing.remote import RemoteRouter

PROVIDER_KEY = "fake-provider-secret-value-42"
SECRET = "s3cr3t-in-the-url"


@pytest.mark.parametrize(
    "url",
    [
        "https://router.example.com/route",
        "http://localhost:8765/route",
        "http://127.0.0.1/route",
        "http://127.8.9.10/route",
        "http://[::1]:9000/route",
    ],
)
def test_https_or_localhost_or_a_loopback_literal_is_accepted(url: str) -> None:
    assert RemoteRouter(url=url).router_name.startswith("remote(")


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("http://router.example.com/route", "must use https"),
        ("http://10.0.0.5/route", "must use https"),
        ("http://0.0.0.0/route", "must use https"),
        ("http://foo.localhost/route", "must use https"),
        ("http://localhost.evil.com/route", "must use https"),
        ("http://127.0.0.1@evil.com/route", "not an absolute http"),
        ("http://evil.com\\@localhost/route", "not an absolute http"),
        ("http://local%68ost/route", "not an absolute http"),
        ("http:// localhost/route", "not an absolute http"),
        ("ftp://router.example.com/route", "not an absolute http"),
        ("/route", "not an absolute http"),
        ("https:///route", "not an absolute http"),
        ("https://router.example.com/route?token=abc", "may not carry"),
        ("https://router.example.com/route#frag", "may not carry"),
        ("", "needs router_config"),
    ],
)
def test_anything_else_is_refused_when_the_router_is_built(url: str, says: str) -> None:
    with pytest.raises(ConfigurationError, match=says):
        WebRouter(router="remote", router_config={"url": url})


@pytest.mark.parametrize(
    "url",
    [f"https://user:{SECRET}@router.example.com/route", f"https://router.example.com/route?token={SECRET}"],
)
def test_a_refusal_echoes_nothing_of_the_url(url: str) -> None:
    with pytest.raises(ConfigurationError) as refused:
        RemoteRouter(url=url)
    assert SECRET not in str(refused.value) and "router.example.com" not in str(refused.value)
    assert refused.value.__cause__ is None and refused.value.__context__ is None


@pytest.mark.parametrize("value", [0, -1, float("inf"), True, "1"])
def test_timeout_s_must_be_a_positive_number(value: Any) -> None:
    with pytest.raises(ConfigurationError, match="timeout_s"):
        RemoteRouter(url="https://router.example.com", timeout_s=value)


def test_the_record_and_repr_name_the_service_by_scheme_and_host_only() -> None:
    router = RemoteRouter(url="https://Router.Example.com:8443/v1/route", api_key=KEY)
    assert router.router_name == "remote(https://router.example.com)"
    for shown in (repr(router), router.router_name):
        for hidden in ("8443", "v1", KEY):
            assert hidden not in shown


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
        return {"schema": 1, "tools": ["search"], "reason": f"key {KEY}", "exclude": {"search:exa": KEY, KEY: "x"}}

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


# -- the URLs a service may set --------------------------------------------------------


def _sets_urls(urls: list[str]) -> Any:
    return lambda body: {"schema": 1, "tools": ["extract"], "params": {"extract": {"urls": urls}}}


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/"])
def test_a_service_cannot_point_extract_at_this_network_with_no_url_policy(url: str) -> None:
    with running(_sets_urls([url])) as server:
        plan = WebRouter(router="remote", router_config=config(server)).plan("read https://example.com/a")
    assert url not in plan.params.get("urls", []), "held to public_only although the caller set no url_policy"
    assert "refused by the URL policy" in (plan.decision.ignored or {})["params.extract.urls"], "noted in the record"


def test_a_service_may_set_public_urls() -> None:
    with running(_sets_urls(["https://example.org/b"])) as server:
        plan = WebRouter(router="remote", router_config=config(server)).plan("read https://example.com/a")
    assert plan.params["urls"] == ["https://example.org/b"]


def test_an_in_process_router_keeps_the_callers_default_url_policy() -> None:
    def mine(req: RouteRequest) -> RouteDecision:
        return RouteDecision(tools=["extract"], params={"extract": {"urls": ["http://127.0.0.1/"]}})

    plan = WebRouter(router=mine).plan("read https://example.com/a")
    assert plan.params["urls"] == ["http://127.0.0.1/"], "the default is unchanged for the caller's own code"


def test_without_httpx_the_error_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_httpx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx":
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_httpx)
    with pytest.raises(ConfigurationError, match=r"webrouter\[remote\]"):
        RemoteRouter(url="https://router.example.com")


def test_importing_web_router_does_not_import_the_remote_router() -> None:
    import subprocess
    import sys

    code = "import sys, web_router; print('web_router.routing.remote' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


# -- the reference server's own guards -------------------------------------------------


def test_the_reference_server_binds_loopback_and_turns_away_a_foreign_host_no_key_or_a_large_body() -> None:
    import inspect

    import httpx

    from tests.routing_remote_helpers import reference

    assert inspect.signature(reference.serve).parameters["host"].default == "127.0.0.1"
    body = {"schema": 1, "prompt": "x", "candidates": [], "deadline_s": 1.0}
    with running() as server, httpx.Client(trust_env=False) as client:
        auth = {"Authorization": f"Bearer {KEY}"}
        assert client.post(server.url, json=body, headers=auth).status_code == 200
        assert client.post(server.url, json=body).status_code == 401
        assert client.post(server.url, json=body, headers={**auth, "Host": "rebound.example"}).status_code == 421
        big = b"{" + b" " * (1_000_001) + b"}"
        large = {**auth, "Content-Type": "application/json"}
        assert client.post(server.url, content=big, headers=large).status_code == 413


def test_the_reference_server_answers_a_too_deeply_nested_body_with_400() -> None:
    import httpx

    deep = b"[" * 200_000 + b"]" * 200_000  # under the 1 MB cap, deeper than json.loads recurses
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    with running() as server, httpx.Client(trust_env=False) as client:
        answer = client.post(server.url, content=deep, headers=headers)
        assert answer.status_code == 400 and "nested" in answer.json()["error"]
        body = {"schema": 1, "prompt": "x", "candidates": [], "deadline_s": 1.0}
        assert client.post(server.url, json=body, headers=headers).status_code == 200, "the server still answers"


def test_the_example_server_closes_connections_past_its_worker_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reference, "MAX_WORKERS", 2)
    with running() as server:
        address = server.server_address[:2]
        idle = [socket.create_connection(address) for _ in range(2)]  # hold both workers, sending nothing
        time.sleep(0.2)
        with socket.create_connection(address, timeout=2) as third:
            assert third.recv(1) == b"", "a third connection is closed, not given a thread"
        for conn in idle:
            conn.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:  # the workers notice the close and give their slots back
            with socket.create_connection(address, timeout=2) as again:
                again.sendall(b"POST /nowhere HTTP/1.1\r\nHost: " + f"{address[0]}:{address[1]}".encode() + b"\r\n\r\n")
                if again.recv(12).startswith(b"HTTP/1.0 404"):
                    return
            time.sleep(0.05)
        pytest.fail("the slots were never given back")

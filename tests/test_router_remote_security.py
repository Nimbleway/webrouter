"""The ``remote`` router's guards: HTTPS except to loopback, the key never shown, what a service is sent and shown."""

from __future__ import annotations

import builtins
import logging
from typing import Any

import pytest

from tests.routing_remote_helpers import KEY, Recorder, config, running
from web_router import RouterError, WebRouter
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY
from web_router.exceptions import ConfigurationError
from web_router.routing.remote import RemoteRouter

PROVIDER_KEY = "fake-provider-secret-value-42"


@pytest.mark.parametrize(
    "url",
    [
        "https://router.example.com/route",
        "http://localhost:8765/route",
        "http://127.0.0.1/route",
        "http://[::1]:9000/route",
    ],
)
def test_https_or_a_loopback_host_is_accepted(url: str) -> None:
    assert RemoteRouter(url=url).router_name.startswith("remote(")


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("http://router.example.com/route", "must use https"),
        ("http://10.0.0.5/route", "must use https"),
        ("http://localhost.evil.example/route", "must use https"),
        ("ftp://router.example.com/route", "not an absolute http"),
        ("/route", "not an absolute http"),
        ("https:///route", "not an absolute http"),
        ("", "needs router_config"),
    ],
)
def test_anything_else_is_refused_when_the_router_is_built(url: str, says: str) -> None:
    with pytest.raises(ConfigurationError, match=says):
        WebRouter(router="remote", router_config={"url": url})


def test_a_refusal_shows_the_scheme_and_host_only() -> None:
    with pytest.raises(ConfigurationError) as refused:
        RemoteRouter(url="http://user:pa55word@router.example.com/secret/path?token=abc")
    message = str(refused.value)
    assert "http://router.example.com" in message
    for hidden in ("user", "pa55word", "secret", "token", "abc"):
        assert hidden not in message


@pytest.mark.parametrize("value", [0, -1, float("inf"), True, "1"])
def test_timeout_s_must_be_a_positive_number(value: Any) -> None:
    with pytest.raises(ConfigurationError, match="timeout_s"):
        RemoteRouter(url="https://router.example.com", timeout_s=value)


def test_the_record_and_repr_name_the_service_by_scheme_and_host_only() -> None:
    router = RemoteRouter(url="https://user:pw@Router.Example.com:8443/v1/route?tenant=acme", api_key=KEY)
    assert router.router_name == "remote(https://router.example.com)"
    for shown in (repr(router), router.router_name):
        for hidden in ("user", "pw", "8443", "v1", "tenant", "acme", KEY):
            assert hidden not in shown


def test_the_service_gets_its_bearer_key_and_a_wrong_one_is_turned_away(caplog: pytest.LogCaptureFixture) -> None:
    with running() as server:
        caplog.set_level(logging.DEBUG)
        good = WebRouter(router="remote", router_config=config(server)).plan("read https://example.com/a")
        wrong = WebRouter(router="remote", router_config={**config(server), "api_key": "nope"})
        strict = WebRouter(
            router="remote", router_config={**config(server), "api_key": "nope"}, fail_on_router_error=True
        )
        bad = wrong.plan("read https://example.com/a")
        with pytest.raises(RouterError) as raised:
            strict.plan("read https://example.com/a")
    assert good.tool == "extract" and bad.decision.reason == "router_error"
    assert "HTTP 401" in str(raised.value.__cause__)
    assert KEY not in caplog.text and "nope" not in caplog.text


def test_the_key_comes_from_the_environment_when_router_config_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_REMOTE_ROUTER_API_KEY, KEY)
    with running() as server:
        plan = WebRouter(router="remote", router_config={"url": server.url}).plan("read https://example.com/a")
    assert plan.tool == "extract"


def test_no_key_value_reaches_the_service_or_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", PROVIDER_KEY)
    recorder = Recorder()
    with running(recorder) as server:
        wr = WebRouter(router="remote", router_config=config(server))
        plan = wr.plan("solar news")
    record = plan.decision.model_dump_json()
    assert "search:tavily" in recorder.raw[0], "candidate ids are sent by design: they name configured backends"
    for text in (*recorder.raw, record, repr(plan), repr(wr._owned_router)):
        assert PROVIDER_KEY not in text and KEY not in text


def test_a_failure_names_the_host_never_the_url_or_key() -> None:
    with running() as server:
        url = server.url
    router = WebRouter(
        router="remote",
        router_config={"url": url + "?token=hidden", "api_key": KEY},
        fail_on_router_error=True,
    )
    with pytest.raises(RouterError) as raised:
        router.plan("solar news")
    chain = [raised.value, raised.value.__cause__]
    text = " ".join(map(str, chain))
    assert "remote(http://127.0.0.1)" in text and "http://127.0.0.1" in str(raised.value.__cause__)
    assert "hidden" not in text and KEY not in text
    assert raised.value.__cause__.__cause__ is None and raised.value.__cause__.__context__ is None


def test_without_httpx_the_error_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_httpx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx":
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_httpx)
    with pytest.raises(ConfigurationError, match=r"web-router\[remote\]"):
        RemoteRouter(url="https://router.example.com")


def test_importing_web_router_does_not_import_httpx_for_the_router() -> None:
    import subprocess
    import sys

    code = "import sys, web_router; print('web_router.routing.remote' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"

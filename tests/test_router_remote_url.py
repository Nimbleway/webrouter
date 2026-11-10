"""The ``remote`` router's settings: the URL it is given, its timeout and name, httpx, and the URLs it may set."""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from tests.routing_remote_helpers import KEY, config, running
from web_router import RouteDecision, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.remote import RemoteRouter

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

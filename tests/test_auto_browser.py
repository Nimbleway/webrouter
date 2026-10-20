"""`auto` for browser sessions: keyed backends, then `local`, and never a second session once one is open."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests.conftest import FakeBrowserProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import (
    AUTO_BROWSER_PREFERRED,
    BROWSER_ENV_BY_PROVIDER,
    BROWSER_TIMEOUT,
    PROVIDER_ANCHOR,
    PROVIDER_KERNEL,
    PROVIDER_LOCAL,
    PROVIDER_STEEL,
)
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers._http import HttpBrowserProvider
from web_router.providers.auto import AutoBrowserProvider
from web_router.registry import get_browser_provider_class, register_browser_provider, unregister_browser_provider
from web_router.types import BROWSER_PARAMETERS, BrowserRequest, BrowserSession


class _Ledger:
    """What every member of one chain opened and released, in order."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.released: list[str] = []
        self.requests: dict[str, BrowserRequest] = {}
        self.registered: list[str] = []


def _http_failure(status: int) -> ProviderError:
    request = httpx.Request("POST", "https://browser.example.test/sessions")
    original = httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))
    return ProviderError("member", f"HTTP {status}", original=original)


def _lost_answer() -> ProviderError:
    """A create call the backend received and may have acted on, whose answer never arrived."""
    original = httpx.ReadTimeout("read timed out", request=httpx.Request("POST", "https://browser.example.test"))
    return ProviderError("member", "timed out", original=original)


def _member(
    ledger: _Ledger,
    member_name: str,
    *,
    raises: Exception | None = None,
    supported: frozenset[str] = BROWSER_PARAMETERS,
) -> None:
    """Register a chain member that opens a session, or fails to in a given way."""

    class Member(FakeBrowserProvider):
        name = member_name
        supported_parameters = supported
        # Every level where stealth is mapped, none where it is not, as the contract requires.
        stealth_levels = FakeBrowserProvider.stealth_levels if "stealth" in supported else frozenset()

        def _open(self, request: BrowserRequest) -> BrowserSession:
            ledger.requests[member_name] = request
            if raises is not None:
                raise raises
            ledger.opened.append(member_name)
            return super()._open(request)

        def _release(self, session_id: str) -> None:
            ledger.released.append(f"{member_name}/{session_id}")
            super()._release(session_id)

    register_browser_provider(member_name, Member)
    ledger.registered.append(member_name)


@pytest.fixture
def chain() -> Any:
    ledger = _Ledger()
    yield ledger
    # A member left registered would break the contract test that compares the
    # registry with BROWSER_ENV_BY_PROVIDER, from another file.
    for name in ledger.registered:
        unregister_browser_provider(name)


def _auto(*names: str) -> AutoBrowserProvider:
    return AutoBrowserProvider(chain=tuple((name, {}) for name in names))


def test_the_default_chain_is_the_keyed_backends_in_order_then_local(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in BROWSER_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)
    # Set in the opposite order to the chain, so the order cannot come from the environment.
    monkeypatch.setenv(BROWSER_ENV_BY_PROVIDER[PROVIDER_KERNEL][0], "kernel-key")
    monkeypatch.setenv(BROWSER_ENV_BY_PROVIDER[PROVIDER_ANCHOR][0], "anchor-key")
    monkeypatch.setenv(BROWSER_ENV_BY_PROVIDER[PROVIDER_STEEL][0], "steel-key")

    assert AutoBrowserProvider.default_chain() == (
        (PROVIDER_STEEL, {}),
        (PROVIDER_ANCHOR, {}),
        (PROVIDER_KERNEL, {}),
        (PROVIDER_LOCAL, {}),
    )


def test_a_lifetime_passes_kernel_over_before_anything_opens() -> None:
    """Kernel has an idle limit and no lifetime, so `auto(timeout=...)` moves on rather than drop the option."""
    with pytest.raises(UnsupportedParameterError, match="timeout"):
        _auto(PROVIDER_KERNEL).open(BrowserRequest(timeout=120))


def test_with_no_credential_the_chain_is_local_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in BROWSER_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)

    assert AutoBrowserProvider.default_chain() == ((PROVIDER_LOCAL, {}),)


def test_every_preferred_backend_is_a_registered_keyed_browser_provider() -> None:
    for name in AUTO_BROWSER_PREFERRED:
        assert BROWSER_ENV_BY_PROVIDER[name], f"{name} needs no key and belongs after the keyed tier"
        get_browser_provider_class(name)


def test_the_first_member_that_opens_wins_and_owns_the_handle(chain: _Ledger) -> None:
    _member(chain, "first")
    _member(chain, "second")

    with _auto("first", "second").open(BrowserRequest()) as browser:
        assert browser.provider == "first"
        assert browser.session.provider == "first"

    assert chain.opened == ["first"]
    assert chain.released == [f"first/{browser.session_id}"]


async def test_the_async_handle_belongs_to_the_member_that_opened_it(chain: _Ledger) -> None:
    _member(chain, "first", raises=ConfigurationError("no key for first"))
    _member(chain, "second")

    async with await _auto("first", "second").aopen(BrowserRequest()) as browser:
        assert browser.provider == "second"

    assert chain.opened == ["second"]
    assert chain.released == [f"second/{browser.session_id}"]


@pytest.mark.parametrize(
    "refusal",
    [ConfigurationError("no key"), _http_failure(401), _http_failure(422)],
    ids=["no-credential", "unauthorized", "rejected-create"],
)
def test_a_member_that_refused_to_open_is_passed_for_the_next(chain: _Ledger, refusal: Exception) -> None:
    _member(chain, "first", raises=refusal)
    _member(chain, "second")

    with _auto("first", "second").open(BrowserRequest()) as browser:
        assert browser.provider == "second"


@pytest.mark.parametrize("failure", [_lost_answer(), _http_failure(504)], ids=["read-timeout", "gateway-5xx"])
def test_a_create_that_may_have_opened_a_session_is_not_retried_elsewhere(chain: _Ledger, failure: Exception) -> None:
    """The backend may be billing for a session already, so a second would bill the call twice."""
    _member(chain, "first", raises=failure)
    _member(chain, "second")

    with pytest.raises(ProviderError) as caught:
        _auto("first", "second").open(BrowserRequest())

    assert caught.value is failure
    assert "second" not in chain.requests


async def test_an_async_create_that_may_have_opened_a_session_is_not_retried(chain: _Ledger) -> None:
    _member(chain, "first", raises=_lost_answer())
    _member(chain, "second")

    with pytest.raises(ProviderError):
        await _auto("first", "second").aopen(BrowserRequest())

    assert "second" not in chain.requests


def test_when_every_member_refuses_each_reason_is_reported(chain: _Ledger) -> None:
    _member(chain, "first", raises=ConfigurationError("no key for first"))
    _member(chain, "second", raises=_http_failure(403))

    with pytest.raises(ProviderError, match="no key for first") as caught:
        _auto("first", "second").open(BrowserRequest())

    assert "HTTP 403" in str(caught.value)
    assert chain.opened == []


def test_a_member_lacking_an_option_the_caller_set_is_skipped_unopened(chain: _Ledger) -> None:
    _member(chain, "lifetime-only", supported=frozenset({"timeout"}))
    _member(chain, "idle-too", supported=frozenset({"timeout", "idle_timeout"}))

    with _auto("lifetime-only", "idle-too").open(BrowserRequest(idle_timeout=60)) as browser:
        assert browser.provider == "idle-too"

    assert "lifetime-only" not in chain.requests, "a skipped member must not even be asked"


def test_when_nothing_is_eligible_the_error_says_why_each_member_was_skipped(chain: _Ledger) -> None:
    _member(chain, "plain", supported=frozenset({"timeout"}))
    _member(chain, "headful", supported=frozenset({"headless"}))

    with pytest.raises(UnsupportedParameterError) as caught:
        _auto("plain", "headful").open(BrowserRequest(stealth="low", headless=False))

    message = str(caught.value)
    assert "plain does not map 'headless', 'stealth'" in message
    assert "headful does not map 'stealth'" in message
    assert caught.value.parameters == ["headless", "stealth"]
    assert chain.requests == {}


def test_the_default_timeout_does_not_count_as_a_caller_option(chain: _Ledger) -> None:
    """Our ten-minute default must not pass over a backend with no lifetime limit.

    Each member still applies it for itself, as it would if named directly.
    """
    _member(chain, "idle-only", supported=frozenset({"idle_timeout"}))
    _member(chain, "lifetime", supported=frozenset({"timeout"}))

    with _auto("idle-only").open(BrowserRequest()) as browser:
        assert browser.provider == "idle-only"
    with _auto("lifetime").open(BrowserRequest()):
        pass

    assert chain.requests["idle-only"].timeout is None
    assert chain.requests["lifetime"].timeout == BROWSER_TIMEOUT


def test_a_timeout_the_caller_set_does_count(chain: _Ledger) -> None:
    _member(chain, "idle-only", supported=frozenset({"idle_timeout"}))
    _member(chain, "lifetime", supported=frozenset({"timeout"}))

    with _auto("idle-only", "lifetime").open(BrowserRequest(timeout=BROWSER_TIMEOUT)) as browser:
        assert browser.provider == "lifetime"


def test_provider_options_are_refused_before_anything_opens(chain: _Ledger) -> None:
    """They are one backend's fields, and `local` would read them as Chrome switches."""
    _member(chain, "first")

    with pytest.raises(UnsupportedParameterError, match="Name the backend"):
        _auto("first").open(BrowserRequest(provider_options={"solveCaptcha": True}))

    assert chain.requests == {}


@pytest.mark.parametrize(
    "request_fields",
    [{"profile": "ctx-1"}, {"profile": "ctx-1", "persist_profile": True}, {"persist_profile": False}],
)
def test_a_saved_profile_is_refused_before_anything_opens(chain: _Ledger, request_fields: dict[str, Any]) -> None:
    """A profile is saved on one backend; a member that maps profiles is no sign it holds this one."""
    _member(chain, "first")

    with pytest.raises(UnsupportedParameterError, match="Name the backend the profile is saved on") as caught:
        _auto("first").open(BrowserRequest(**request_fields))

    assert caught.value.parameters == sorted(request_fields)
    assert chain.requests == {}


async def test_async_the_default_timeout_does_not_count_as_a_caller_option(chain: _Ledger) -> None:
    _member(chain, "idle-only", supported=frozenset({"idle_timeout"}))
    _member(chain, "lifetime", supported=frozenset({"timeout"}))

    async with await _auto("idle-only").aopen(BrowserRequest()) as browser:
        assert browser.provider == "idle-only"
    async with await _auto("lifetime").aopen(BrowserRequest()):
        pass

    assert chain.requests["idle-only"].timeout is None
    assert chain.requests["lifetime"].timeout == BROWSER_TIMEOUT


async def test_async_provider_options_are_refused_before_anything_opens(chain: _Ledger) -> None:
    _member(chain, "first")

    with pytest.raises(UnsupportedParameterError, match="Name the backend"):
        await _auto("first").aopen(BrowserRequest(provider_options={"solveCaptcha": True}))

    assert chain.requests == {}


def test_releasing_through_auto_itself_is_refused_rather_than_guessed(chain: _Ledger) -> None:
    _member(chain, "first")
    provider = _auto("first")

    with provider.open(BrowserRequest()) as browser, pytest.raises(ProviderError, match="holds no session"):
        provider.release(browser.session_id)


def test_auto_is_the_router_default_for_open_browser(chain: _Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    _member(chain, "first")
    monkeypatch.setattr(AutoBrowserProvider, "default_chain", staticmethod(lambda: (("first", {}),)))

    router = WebRouter()
    browser = router.open_browser()
    assert browser.provider == "first"
    router.close()

    assert chain.released == [f"first/{browser.session_id}"]


async def test_auto_is_the_async_router_default_for_open_browser(
    chain: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    _member(chain, "first")
    monkeypatch.setattr(AutoBrowserProvider, "default_chain", staticmethod(lambda: (("first", {}),)))

    router = AsyncWebRouter()
    browser = await router.open_browser()
    assert browser.provider == "first"
    await router.aclose()

    assert chain.released == [f"first/{browser.session_id}"]


# -- Through the real HTTP wrapping ------------------------------------------
# The fakes above raise ProviderError by hand. These go through
# HttpBrowserProvider, so the classifier sees exactly what a vendor's failure
# looks like once the base class has wrapped it.

HTTP_MEMBER = "http-member"


class _HttpMember(HttpBrowserProvider):
    name = HTTP_MEMBER
    base_url = "https://browser.example.test"
    open_path = "/sessions"
    release_path = "/sessions/{session_id}"
    release_method = "DELETE"

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return {}

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        return BrowserSession(provider=self.name, session_id=payload["id"], cdp_url=payload["cdp"])


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _answer(status: int, body: bytes = b"{}") -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, content=body)


FALLS_THROUGH = {
    "connect-error": _connect_error,
    "4xx": _answer(401, b'{"error": "bad key"}'),
}

RAISES = {
    "5xx": _answer(502, b"bad gateway"),
    "bad-json": _answer(200, b"<html>not json</html>"),
    "missing-id": _answer(200, b'{"cdp": "wss://x.example.test"}'),
}


@pytest.fixture
def http_chain(chain: _Ledger) -> Any:
    register_browser_provider(HTTP_MEMBER, _HttpMember)
    _member(chain, "second")
    yield chain
    unregister_browser_provider(HTTP_MEMBER)


def _http_auto(handler: Callable[[httpx.Request], httpx.Response]) -> AutoBrowserProvider:
    options = {"api_key": "test-key", "transport": httpx.MockTransport(handler)}
    return AutoBrowserProvider(chain=((HTTP_MEMBER, options), ("second", {})))


@pytest.mark.parametrize("handler", FALLS_THROUGH.values(), ids=FALLS_THROUGH.keys())
def test_a_real_http_refusal_falls_through(http_chain: _Ledger, handler: Any) -> None:
    with _http_auto(handler).open(BrowserRequest()) as browser:
        assert browser.provider == "second"


@pytest.mark.parametrize("handler", FALLS_THROUGH.values(), ids=FALLS_THROUGH.keys())
async def test_a_real_http_refusal_falls_through_async(http_chain: _Ledger, handler: Any) -> None:
    async with await _http_auto(handler).aopen(BrowserRequest()) as browser:
        assert browser.provider == "second"


@pytest.mark.parametrize("handler", RAISES.values(), ids=RAISES.keys())
def test_a_real_http_failure_that_may_have_opened_a_session_raises(http_chain: _Ledger, handler: Any) -> None:
    with pytest.raises(ProviderError, match=HTTP_MEMBER):
        _http_auto(handler).open(BrowserRequest())

    assert "second" not in http_chain.requests


@pytest.mark.parametrize("handler", RAISES.values(), ids=RAISES.keys())
async def test_a_real_http_failure_that_may_have_opened_a_session_raises_async(
    http_chain: _Ledger, handler: Any
) -> None:
    with pytest.raises(ProviderError, match=HTTP_MEMBER):
        await _http_auto(handler).aopen(BrowserRequest())

    assert "second" not in http_chain.requests

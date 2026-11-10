"""Browserbase browser provider tests.

The base mapping (``timeout``) is derived from Browserbase's OpenAPI
reference, not from a live call -- no account was available during
development; Browserbase documents no idle timeout, so ``idle_timeout`` is
refused.

``stealth``, ``solve_captchas`` and ``proxy_country`` are **unverified**
too, read from the same reference plus its stealth and proxies guides on
2026-09-24:
https://docs.browserbase.com/reference/api/create-a-session,
https://docs.browserbase.com/features/stealth-mode,
https://docs.browserbase.com/platform/identity/overview,
https://docs.browserbase.com/platform/identity/proxies. One thing the docs
contradicted in the spec this was built from: the spec named
``browserSettings.advancedStealth`` for the stealth levels, but the reference
marks that field "Deprecated: use `verified` instead" and still lists
``browserSettings.verified`` as the same boolean, Scale-plan-gated toggle --
so ``verified`` is what is mapped, not the deprecated field. The live integration test below
is the check that confirms them; see CONTRIBUTING.md on verifying against the live API.

``profile``, ``persist_profile`` and the control URL were checked live on
2026-10-20, by ``test_live_a_login_saved_to_a_context_is_there_in_the_next_session``:
a cookie and a localStorage entry set in a session that saved to the context were
there in the next session opened on it.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tests.providers._browser_live import astate_after_release, login_survives, state_after_release
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_browserbase import BROWSERBASE_BASE_URL
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
)
from web_router.providers.browserbase import BrowserbaseBrowserProvider
from web_router.types import BrowserRequest

KEY = "test-key"
SESSION_ID = "123e4567-e89b-12d3-a456-426614174000"
SIGNING_KEY = "signing-key-for-this-session"
CONNECT_URL = f"wss://connect.usw2.browserbase.com?signingKey={SIGNING_KEY}"

SESSION: dict[str, Any] = {
    "id": SESSION_ID,
    "createdAt": "2026-09-23T10:00:00.000Z",
    "updatedAt": "2026-09-23T10:00:00.000Z",
    "projectId": "project-1",
    "startedAt": "2026-09-23T10:00:00.000Z",
    "expiresAt": "2026-09-23T10:10:00.000Z",
    "status": "RUNNING",
    "proxyBytes": 0,
    "keepAlive": False,
    "region": "us-west-2",
}
DEBUG_URL = (
    f"https://www.browserbase.com/devtools-fullscreen/inspector.html?wss=connect.browserbase.com/debug/{SESSION_ID}"
)
DEBUG: dict[str, Any] = {
    "debuggerFullscreenUrl": DEBUG_URL,
    "debuggerUrl": DEBUG_URL.replace("-fullscreen", ""),
    "wsUrl": f"wss://connect.browserbase.com/debug/{SESSION_ID}",
    "pages": [],
}
OPENED: dict[str, Any] = {
    **SESSION,
    "connectUrl": CONNECT_URL,
    "seleniumRemoteUrl": "https://connect.usw2.browserbase.com/webdriver",
    "signingKey": SIGNING_KEY,
}


class _Browserbase:
    """A recorded Browserbase API: answers each call from the payloads above and remembers what it was sent."""

    def __init__(self, status: str = "RUNNING", release_status: int = 200) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.headers: list[httpx.Headers] = []
        self.status = status
        self.release_status = release_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        self.headers.append(request.headers)
        if request.url.path == "/v1/sessions":
            return httpx.Response(201, json=OPENED)
        if request.url.path.endswith("/debug"):
            return httpx.Response(200, json=DEBUG)
        if request.method == "POST":
            return httpx.Response(self.release_status, json={**SESSION, "status": "COMPLETED"})
        return httpx.Response(200, json={**SESSION, "status": self.status})


@pytest.fixture
def browserbase(monkeypatch: pytest.MonkeyPatch) -> _Browserbase:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    return _Browserbase()


def _provider(api: _Browserbase) -> BrowserbaseBrowserProvider:
    return BrowserbaseBrowserProvider(transport=httpx.MockTransport(api))


def test_the_key_is_sent_in_browserbases_own_header_on_every_call(browserbase: _Browserbase) -> None:
    with _provider(browserbase).open(BrowserRequest()):
        pass

    assert [headers["x-bb-api-key"] for headers in browserbase.headers] == [KEY, KEY]
    assert "authorization" not in browserbase.headers[0]


def test_the_timeout_is_sent_as_whole_seconds(browserbase: _Browserbase) -> None:
    payload = _provider(browserbase)._build_payload(BrowserRequest(timeout=90.4))
    assert payload == {"timeout": 91}, "rounded up, never shorter than asked"
    assert type(payload["timeout"]) is int


def test_a_fraction_of_a_second_still_sends_a_second(browserbase: _Browserbase) -> None:
    """Browserbase's 60-second minimum is its to enforce; ours is only never to shorten."""
    assert _provider(browserbase)._build_payload(BrowserRequest(timeout=0.4)) == {"timeout": 1}


def test_the_default_lifetime_reaches_the_wire_as_an_integer(browserbase: _Browserbase) -> None:
    """BROWSER_TIMEOUT is a float of seconds; Browserbase takes an integer, and no project id is needed."""
    with _provider(browserbase).open(BrowserRequest()):
        pass

    assert browserbase.calls[0][:2] == ("POST", "/v1/sessions")
    sent = browserbase.calls[0][2]
    assert sent == {"timeout": 600}
    assert type(sent["timeout"]) is int


def test_a_lifetime_in_provider_options_collides_with_the_default_and_raises(browserbase: _Browserbase) -> None:
    """With no ``timeout=``, the lifetime is still sent from BROWSER_TIMEOUT, so the native field would fight it."""
    with pytest.raises(ConfigurationError, match="pass it as timeout="):
        _provider(browserbase).open(BrowserRequest(provider_options={"timeout": 5}))
    assert browserbase.calls == [], "refused before a session is opened and billed"


def test_provider_options_reach_browserbase_untranslated(browserbase: _Browserbase) -> None:
    options = {"browserSettings": {"solveCaptchas": False}, "keepAlive": True}
    payload = _provider(browserbase)._build_payload(BrowserRequest(timeout=60, provider_options=options))
    assert payload == {"timeout": 60, **options}


@pytest.mark.parametrize(
    "option",
    [
        {"idle_timeout": 30},
        {"viewport": {"width": 800, "height": 600}},
        {"headless": True},
    ],
)
def test_options_no_live_call_confirmed_are_rejected_not_dropped(
    browserbase: _Browserbase, option: dict[str, Any]
) -> None:
    with pytest.raises(UnsupportedParameterError):
        _provider(browserbase).open(BrowserRequest(**option))
    assert browserbase.calls == [], "rejected before a session is opened and billed"


# -- stealth, solve_captchas, proxy_country -----------------------------------


def test_stealth_low_sends_verified_false(browserbase: _Browserbase) -> None:
    """Sent explicitly rather than left unset, so `low` reads as the ordinary session, not "whatever comes default"."""
    payload = _provider(browserbase)._build_payload(BrowserRequest(stealth="low"))
    assert payload == {"browserSettings": {"verified": False}}


def test_stealth_high_sends_verified_true(browserbase: _Browserbase) -> None:
    payload = _provider(browserbase)._build_payload(BrowserRequest(stealth="high"))
    assert payload == {"browserSettings": {"verified": True}}


def test_stealth_off_is_refused_before_a_session_opens(browserbase: _Browserbase) -> None:
    """The reference gives no field to turn baseline evasion off, so `off` must not be served as `low`."""
    with pytest.raises(UnsupportedParameterError, match="does not support stealth='off'") as caught:
        _provider(browserbase).open(BrowserRequest(stealth="off"))

    assert caught.value.values == {"stealth": "off"}
    assert browserbase.calls == []


def test_solve_captchas_false_is_sent_explicitly(browserbase: _Browserbase) -> None:
    payload = _provider(browserbase)._build_payload(BrowserRequest(solve_captchas=False))
    assert payload == {"browserSettings": {"solveCaptchas": False}}


def test_solve_captchas_true_is_sent_explicitly(browserbase: _Browserbase) -> None:
    payload = _provider(browserbase)._build_payload(BrowserRequest(solve_captchas=True))
    assert payload == {"browserSettings": {"solveCaptchas": True}}


def test_proxy_country_turns_the_proxy_on_with_its_geolocation(browserbase: _Browserbase) -> None:
    """Browserbase has no separate proxy switch: sending `proxies` is what turns it on."""
    payload = _provider(browserbase)._build_payload(BrowserRequest(proxy_country="de"))
    assert payload == {"proxies": [{"type": "browserbase", "geolocation": {"country": "DE"}}]}


def test_proxy_country_has_no_published_list_so_any_code_is_sent(browserbase: _Browserbase) -> None:
    payload = _provider(browserbase)._build_payload(BrowserRequest(proxy_country="zz"))
    assert payload["proxies"][0]["geolocation"]["country"] == "ZZ"


def test_a_proxies_key_in_provider_options_alongside_proxy_country_raises(browserbase: _Browserbase) -> None:
    """`proxies` is a list leaf: it is never merged with the one `proxy_country` produces."""
    options = {"proxies": [{"type": "custom"}]}
    with pytest.raises(ConfigurationError, match="set it in one place"):
        _provider(browserbase).open(BrowserRequest(proxy_country="US", provider_options=options))
    assert browserbase.calls == []


def test_a_verified_key_in_provider_options_alongside_stealth_raises(browserbase: _Browserbase) -> None:
    with pytest.raises(ConfigurationError, match="set it in one place"):
        _provider(browserbase).open(
            BrowserRequest(stealth="high", provider_options={"browserSettings": {"verified": False}})
        )
    assert browserbase.calls == []


def test_a_solve_captchas_key_in_provider_options_alongside_the_option_raises(browserbase: _Browserbase) -> None:
    with pytest.raises(ConfigurationError, match="set it in one place"):
        _provider(browserbase).open(
            BrowserRequest(solve_captchas=True, provider_options={"browserSettings": {"solveCaptchas": False}})
        )
    assert browserbase.calls == []


def test_stealth_and_solve_captchas_merge_beneath_the_same_browser_settings_branch(browserbase: _Browserbase) -> None:
    """Two rows under `browserSettings` must land beside each other, not overwrite one another."""
    payload = _provider(browserbase)._build_payload(BrowserRequest(stealth="high", solve_captchas=False))
    assert payload == {"browserSettings": {"verified": True, "solveCaptchas": False}}


def test_the_cdp_url_is_the_connect_url_and_stays_secret(browserbase: _Browserbase) -> None:
    with _provider(browserbase).open(BrowserRequest()) as browser:
        assert browser.cdp_url == CONNECT_URL
        assert SIGNING_KEY not in repr(browser)
        assert SIGNING_KEY not in repr(browser.session)
        assert SIGNING_KEY not in browser.session.model_dump_json()


def test_the_session_is_normalized_with_browserbases_own_expiry(browserbase: _Browserbase) -> None:
    with _provider(browserbase).open(BrowserRequest()) as browser:
        session = browser.session

    assert session.provider == "browserbase"
    assert session.session_id == SESSION_ID
    assert session.expires_at == datetime(2026, 9, 23, 10, 10, tzinfo=UTC)
    assert session.live_view_url is None, "Browserbase's live view grants control, so it is never shown"
    assert session.control_url is None, "the live view is a second request, which open does not make"
    assert session.raw == OPENED


@pytest.mark.parametrize("expires_at", ["next tuesday", 1790000000, None])
def test_an_unreadable_expiry_leaves_expires_at_unset_and_keeps_the_session(
    monkeypatch: pytest.MonkeyPatch, expires_at: Any
) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Browserbase()
    opened = {**OPENED, "expiresAt": expires_at}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions":
            api.calls.append((request.method, request.url.path, None))
            return httpx.Response(201, json=opened)
        return api(request)

    with BrowserbaseBrowserProvider(transport=httpx.MockTransport(answer)).open(BrowserRequest()) as browser:
        assert browser.session.expires_at is None
        assert browser.cdp_url == CONNECT_URL

    assert [method for method, _, _ in api.calls] == ["POST", "POST"], "opened once, released once"


def test_an_open_answer_without_a_connect_url_releases_the_session_it_started(browserbase: _Browserbase) -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions":
            browserbase.calls.append((request.method, request.url.path, None))
            return httpx.Response(201, json=SESSION)
        return browserbase(request)

    with pytest.raises(ProviderError):
        BrowserbaseBrowserProvider(transport=httpx.MockTransport(api)).open(BrowserRequest())

    assert browserbase.calls[-1] == ("POST", f"/v1/sessions/{SESSION_ID}", {"status": "REQUEST_RELEASE"})


def test_a_session_is_released_by_asking_for_its_release(browserbase: _Browserbase) -> None:
    with _provider(browserbase).open(BrowserRequest(timeout=60)):
        pass

    assert browserbase.calls[1:] == [
        ("POST", f"/v1/sessions/{SESSION_ID}", {"status": "REQUEST_RELEASE"}),
    ]


async def test_an_async_session_is_opened_then_released(browserbase: _Browserbase) -> None:
    async with await _provider(browserbase).aopen(BrowserRequest(timeout=60)) as browser:
        assert await browser.status() == "running"

    assert browserbase.calls == [
        ("POST", "/v1/sessions", {"timeout": 60}),
        ("GET", f"/v1/sessions/{SESSION_ID}", None),
        ("POST", f"/v1/sessions/{SESSION_ID}", {"status": "REQUEST_RELEASE"}),
    ]


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("PENDING", "starting"),
        ("RUNNING", "running"),
        ("COMPLETED", "released"),
        ("TIMED_OUT", "expired"),
        ("ERROR", "failed"),
    ],
)
def test_status_becomes_the_normalized_state(monkeypatch: pytest.MonkeyPatch, status: str, state: str) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    assert _provider(_Browserbase(status)).status(SESSION_ID) == state


def test_an_unrecognized_status_raises_rather_than_passing_for_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    with pytest.raises(ProviderError, match="unrecognized session state"):
        _provider(_Browserbase("REQUEST_RELEASE")).status(SESSION_ID)


@pytest.mark.parametrize(
    "status", ["COMPLETED", "TIMED_OUT", "ERROR"], ids=["client-disconnected", "timed-out", "crashed"]
)
def test_releasing_a_session_that_is_already_over_is_not_an_error(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """A client disconnect ends a Browserbase session as COMPLETED, so this is the usual path, not a rare one."""
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Browserbase(status, release_status=400)

    _provider(api).release(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["POST", "GET"]


@pytest.mark.parametrize(
    "status", ["COMPLETED", "TIMED_OUT", "ERROR"], ids=["client-disconnected", "timed-out", "crashed"]
)
async def test_releasing_a_session_that_is_already_over_asynchronously_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Browserbase(status, release_status=400)

    await _provider(api).arelease(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["POST", "GET"]


def test_a_refused_release_of_a_live_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)

    with pytest.raises(ProviderError, match="HTTP 500"):
        _provider(_Browserbase(release_status=500)).release(SESSION_ID)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
def test_live_session_opens_reports_and_releases() -> None:
    provider = BrowserbaseBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(provider, timeout=60) as browser:
            assert browser.cdp_url.startswith("wss://")
            assert browser.session.expires_at is not None
            assert browser.status() in {"starting", "running"}
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
def test_live_session_with_stealth_solve_captchas_and_proxy_country() -> None:
    """The check that confirms the mappings: opens with all three, reads the session back, and probes a bot-check page.

    cookiesfordays.com/halloween-sugar-cookies sits behind a challenge whose
    title reads "Just a moment..." until it clears; a title that moves past
    that is the live signal that `stealth="high"` (`browserSettings.verified`)
    actually changed what the vendor served, not just what the request said.
    """
    provider = BrowserbaseBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(
            provider, timeout=60, stealth="high", solve_captchas=True, proxy_country="US"
        ) as browser:
            assert browser.status() in {"starting", "running"}
            title = browser.goto("https://cookiesfordays.com/halloween-sugar-cookies").title
            assert not title.startswith("Just a moment"), title
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
async def test_live_async_session_opens_and_releases() -> None:
    provider = BrowserbaseBrowserProvider()
    async with AsyncWebRouter() as router:
        async with await router.open_browser(provider, timeout=60) as browser:
            assert await browser.status() in {"starting", "running"}
        assert browser.released

        assert await astate_after_release(provider, browser.session_id) == "released"


def test_a_profile_is_a_context_sent_with_whether_to_persist_it(browserbase: _Browserbase) -> None:
    payload = _provider(browserbase)._build_payload(
        BrowserRequest(profile="ctx-1", persist_profile=True, stealth="low")
    )
    assert payload == {"browserSettings": {"verified": False, "context": {"id": "ctx-1", "persist": True}}}


@pytest.mark.parametrize("persist", [True, False])
def test_persist_profile_without_a_profile_is_refused_before_a_session_opens(
    browserbase: _Browserbase, persist: bool
) -> None:
    with pytest.raises(MissingParameterError, match="profile"):
        _provider(browserbase).open(BrowserRequest(persist_profile=persist))
    assert browserbase.calls == []


def test_the_control_url_is_asked_for_once_and_only_when_wanted(browserbase: _Browserbase) -> None:
    with _provider(browserbase).open(BrowserRequest()) as browser:
        assert browser.control_url() == DEBUG_URL
        assert browser.control_url() == DEBUG_URL

    assert [path for _, path, _ in browserbase.calls].count(f"/v1/sessions/{SESSION_ID}/debug") == 1
    assert SESSION_ID in DEBUG_URL and DEBUG_URL not in repr(browser)


def test_a_released_handle_does_not_ask_for_a_control_url(browserbase: _Browserbase) -> None:
    with _provider(browserbase).open(BrowserRequest()) as browser:
        pass

    assert browser.control_url() is None
    assert not any(path.endswith("/debug") for _, path, _ in browserbase.calls)


def test_an_empty_link_in_the_debug_answer_is_no_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Browserbase()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/debug"):
            return httpx.Response(200, json={**DEBUG, "debuggerFullscreenUrl": ""})
        return api(request)

    with BrowserbaseBrowserProvider(transport=httpx.MockTransport(answer)).open(BrowserRequest()) as browser:
        assert browser.control_url() is None


def test_a_debug_answer_without_a_link_is_asked_for_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Browserbase()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/debug"):
            api.calls.append((request.method, request.url.path, None))
            return httpx.Response(200, json={"pages": []})
        return api(request)

    with BrowserbaseBrowserProvider(transport=httpx.MockTransport(answer)).open(BrowserRequest()) as browser:
        assert browser.control_url() is None
        assert browser.control_url() is None

    assert [path for _, path, _ in api.calls].count(f"/v1/sessions/{SESSION_ID}/debug") == 1


async def test_an_async_handle_asks_for_the_control_url_too(browserbase: _Browserbase) -> None:
    async with await _provider(browserbase).aopen(BrowserRequest()) as browser:
        assert await browser.control_url() == DEBUG_URL

    assert ("GET", f"/v1/sessions/{SESSION_ID}/debug", None) in browserbase.calls


def test_a_failed_control_url_request_is_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", KEY)
    api = _Browserbase()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/debug"):
            return httpx.Response(404, json={"error": "session not running"})
        return api(request)

    with (
        BrowserbaseBrowserProvider(transport=httpx.MockTransport(answer)).open(BrowserRequest()) as browser,
        pytest.raises(ProviderError, match="HTTP 404"),
    ):
        browser.control_url()


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BROWSERBASE_API_KEY"), reason="BROWSERBASE_API_KEY not set")
def test_live_a_login_saved_to_a_context_is_there_in_the_next_session() -> None:
    """Creates a throwaway context through Browserbase's Contexts API, and deletes it afterwards."""
    provider = BrowserbaseBrowserProvider()
    contexts = httpx.Client(base_url=BROWSERBASE_BASE_URL, headers={"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]})
    context = contexts.post("/v1/contexts", json={}).raise_for_status().json()
    try:
        with WebRouter() as router:
            assert login_survives(router, provider, context["id"], timeout=60)
    finally:
        contexts.delete(f"/v1/contexts/{context['id']}")
        contexts.close()

"""Hyperbrowser browser provider tests.

``timeout`` is derived from Hyperbrowser's OpenAPI reference and the
hyperbrowser Python SDK's models, not from a live call -- no account was
available during development. ``stealth``, ``solve_captchas`` and
``proxy_country`` are derived from
https://hyperbrowser.ai/docs/api-reference/create-new-session and
https://hyperbrowser.ai/docs/sessions/parameters, read 2026-09-24 --
**unverified against a live call**. ``test_live_session_opens_with_stealth_captchas_and_a_proxy_country`` below is
the check that confirms them against a real account. The recorded payloads below are
hand-written from that schema, including the shape of ``wsEndpoint``, which the
docs never show in full but append ``&keepAlive=true`` to, so it already
carries a query string, and ``launchState``, which the create-session reference
says echoes ``useStealth``, ``useUltraStealth``, ``useProxy`` and
``solveCaptchas`` back. Hyperbrowser has no idle timeout, so ``idle_timeout``
stays unmapped. See CONTRIBUTING.md on verifying against the live API.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from tests.providers._browser_live import astate_after_release, state_after_release
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import BROWSER_TIMEOUT
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.hyperbrowser import HyperbrowserBrowserProvider
from web_router.types import BrowserRequest

KEY = "test-key"
SESSION_ID = "123e4567-e89b-12d3-a456-426614174000"
SESSION_TOKEN = "session-token-abc"

OPENED: dict[str, Any] = {
    "id": SESSION_ID,
    "teamId": "00000000-0000-0000-0000-000000000001",
    "status": "active",
    "startTime": 1790157600000,
    "endTime": None,
    "createdAt": "2026-09-23T10:00:00Z",
    "updatedAt": "2026-09-23T10:00:00Z",
    "launchState": {"useStealth": False, "useProxy": False, "solveCaptchas": False},
    "creditsUsed": None,
    "sessionUrl": f"https://app.hyperbrowser.ai/features/sessions/{SESSION_ID}",
    "liveUrl": f"https://app.hyperbrowser.ai/live?token={SESSION_TOKEN}",
    "token": SESSION_TOKEN,
    "wsEndpoint": f"wss://connect.hyperbrowser.ai/?token={SESSION_TOKEN}",
    "webdriverEndpoint": f"https://connect.hyperbrowser.ai/webdriver?token={SESSION_TOKEN}",
    "computerActionEndpoint": f"https://connect.hyperbrowser.ai/computer-action?token={SESSION_TOKEN}",
}


class _Hyperbrowser:
    """A recorded Hyperbrowser API: answers each call from the payloads above and remembers what it was sent."""

    def __init__(
        self,
        reported: str = "active",
        release_status: int = 200,
        release_body: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.headers: list[httpx.Headers] = []
        self.reported = reported
        self.release_status = release_status
        self.release_body = release_body if release_body is not None else {"success": True}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        self.headers.append(request.headers)
        if request.url.path.endswith("/stop"):
            return httpx.Response(self.release_status, json=self.release_body)
        if request.method == "GET":
            return httpx.Response(200, json={**OPENED, "status": self.reported})
        return httpx.Response(200, json=OPENED)


@pytest.fixture
def hyperbrowser(monkeypatch: pytest.MonkeyPatch) -> _Hyperbrowser:
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    return _Hyperbrowser()


def _provider(api: _Hyperbrowser) -> HyperbrowserBrowserProvider:
    return HyperbrowserBrowserProvider(transport=httpx.MockTransport(api))


def test_the_key_is_sent_in_hyperbrowsers_own_header(hyperbrowser: _Hyperbrowser) -> None:
    with _provider(hyperbrowser).open(BrowserRequest()):
        pass

    assert hyperbrowser.headers[0]["x-api-key"] == KEY
    assert "authorization" not in hyperbrowser.headers[0]


def test_the_stop_call_carries_the_key_too(hyperbrowser: _Hyperbrowser) -> None:
    with _provider(hyperbrowser).open(BrowserRequest(timeout=60)):
        pass

    assert hyperbrowser.calls[-1][1] == f"/api/session/{SESSION_ID}/stop"
    assert hyperbrowser.headers[-1]["x-api-key"] == KEY


@pytest.mark.parametrize(("seconds", "minutes"), [(60, 1), (90, 2), (1, 1), (600, 10), (43200, 720)])
def test_seconds_become_whole_minutes_rounded_up(hyperbrowser: _Hyperbrowser, seconds: float, minutes: int) -> None:
    """Hyperbrowser takes whole minutes; rounding down would end a session before the caller's limit."""
    payload = _provider(hyperbrowser)._build_payload(BrowserRequest(timeout=seconds))
    assert payload == {"timeoutMinutes": minutes}
    assert type(payload["timeoutMinutes"]) is int


def test_the_default_lifetime_reaches_the_wire_as_whole_minutes(hyperbrowser: _Hyperbrowser) -> None:
    """BROWSER_TIMEOUT is a float of seconds; Hyperbrowser takes an integer count of minutes."""
    with _provider(hyperbrowser).open(BrowserRequest()):
        pass

    assert hyperbrowser.calls[0] == ("POST", "/api/session", {"timeoutMinutes": round(BROWSER_TIMEOUT / 60)})
    assert type(hyperbrowser.calls[0][2]["timeoutMinutes"]) is int  # type: ignore[index]


def test_a_timeout_past_hyperbrowsers_limit_is_sent_as_is_and_its_refusal_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over 720 minutes is not clamped: Hyperbrowser refuses it, and nothing is opened or billed."""
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    sent: list[tuple[str, dict[str, Any] | None]] = []

    def refuses(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, json.loads(request.content) if request.content else None))
        return httpx.Response(400, json={"message": "timeoutMinutes must be at most 720"})

    provider = HyperbrowserBrowserProvider(transport=httpx.MockTransport(refuses))
    with pytest.raises(ProviderError, match="HTTP 400"):
        provider.open(BrowserRequest(timeout=721 * 60))

    assert sent == [("POST", {"timeoutMinutes": 721})], "sent once, unclamped, and no stop for a session never opened"


def test_a_lifetime_in_provider_options_collides_with_the_default_and_raises(hyperbrowser: _Hyperbrowser) -> None:
    """With no ``timeout=``, the lifetime is still sent from BROWSER_TIMEOUT, so the native field would fight it."""
    with pytest.raises(ConfigurationError, match="pass it as timeout="):
        _provider(hyperbrowser).open(BrowserRequest(provider_options={"timeoutMinutes": 5}))
    assert hyperbrowser.calls == [], "refused before a session is opened and billed"


def test_provider_options_reach_hyperbrowser_untranslated(hyperbrowser: _Hyperbrowser) -> None:
    request = BrowserRequest(timeout=60, provider_options={"useStealth": True})
    assert _provider(hyperbrowser)._build_payload(request) == {"timeoutMinutes": 1, "useStealth": True}


@pytest.mark.parametrize(
    "option",
    [
        {"idle_timeout": 30},
        {"profile": "p-1"},
        {"persist_profile": True},
        {"viewport": {"width": 800, "height": 600}},
        {"headless": True},
    ],
)
def test_options_no_live_call_confirmed_are_rejected_not_dropped(
    hyperbrowser: _Hyperbrowser, option: dict[str, Any]
) -> None:
    with pytest.raises(UnsupportedParameterError):
        _provider(hyperbrowser).open(BrowserRequest(**option))
    assert hyperbrowser.calls == [], "rejected before a session is opened and billed"


# -- stealth, solve_captchas, proxy_country -----------------------------------


def test_stealth_levels_are_declared_from_the_table() -> None:
    assert HyperbrowserBrowserProvider.stealth_levels == frozenset({"off", "low", "high"})


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("off", {"useStealth": False, "useUltraStealth": False}),
        ("low", {"useStealth": True}),
        ("high", {"useUltraStealth": True}),
    ],
)
def test_each_stealth_level_maps_to_its_own_fields(
    hyperbrowser: _Hyperbrowser, level: str, expected: dict[str, Any]
) -> None:
    payload = _provider(hyperbrowser)._build_payload(BrowserRequest(stealth=level))  # type: ignore[arg-type]
    assert payload == expected


@pytest.mark.parametrize("solve", [True, False])
def test_solve_captchas_is_sent_verbatim_true_or_false(hyperbrowser: _Hyperbrowser, solve: bool) -> None:
    """`False` must reach Hyperbrowser explicitly -- a caller turning captcha solving off is a real request."""
    payload = _provider(hyperbrowser)._build_payload(BrowserRequest(solve_captchas=solve))
    assert payload == {"solveCaptchas": solve}


def test_proxy_country_turns_the_proxy_on_and_is_upper_cased(hyperbrowser: _Hyperbrowser) -> None:
    payload = _provider(hyperbrowser)._build_payload(BrowserRequest(proxy_country="us"))
    assert payload == {"useProxy": True, "proxyCountry": "US"}


def test_a_country_outside_the_published_enum_is_refused_before_a_session_opens(hyperbrowser: _Hyperbrowser) -> None:
    with pytest.raises(UnsupportedParameterError, match="proxy_country='ZZ'"):
        _provider(hyperbrowser).open(BrowserRequest(proxy_country="ZZ"))
    assert hyperbrowser.calls == [], "refused before a session is opened and billed"


def test_a_country_in_the_published_enum_is_matched_whatever_its_case(hyperbrowser: _Hyperbrowser) -> None:
    with _provider(hyperbrowser).open(BrowserRequest(proxy_country="de")):
        pass

    sent = hyperbrowser.calls[0][2]
    assert sent is not None
    assert (sent["useProxy"], sent["proxyCountry"]) == (True, "DE")


def test_a_stealth_and_proxy_field_clash_with_provider_options_raises(hyperbrowser: _Hyperbrowser) -> None:
    with pytest.raises(ConfigurationError, match="useStealth"):
        _provider(hyperbrowser).open(BrowserRequest(stealth="low", provider_options={"useStealth": False}))
    assert hyperbrowser.calls == [], "refused before a session is opened and billed"


def test_the_cdp_url_is_the_reported_ws_endpoint_and_stays_secret(hyperbrowser: _Hyperbrowser) -> None:
    with _provider(hyperbrowser).open(BrowserRequest()) as browser:
        assert browser.cdp_url == OPENED["wsEndpoint"]
        assert SESSION_TOKEN not in repr(browser)
        assert SESSION_TOKEN not in repr(browser.session)
        assert SESSION_TOKEN not in browser.session.model_dump_json()


def test_the_session_is_normalized_without_the_control_url(hyperbrowser: _Hyperbrowser) -> None:
    with _provider(hyperbrowser).open(BrowserRequest()) as browser:
        session = browser.session

    assert session.provider == "hyperbrowser"
    assert session.session_id == SESSION_ID
    assert session.live_view_url is None, "liveUrl lets its holder drive the browser, so it stays in raw"
    assert session.expires_at is None, "Hyperbrowser reports no expiry, and one is not worked out for it"
    assert session.raw == OPENED


def test_a_session_is_opened_then_stopped_by_its_id(hyperbrowser: _Hyperbrowser) -> None:
    with _provider(hyperbrowser).open(BrowserRequest(timeout=60)):
        pass

    assert [(method, path) for method, path, _ in hyperbrowser.calls] == [
        ("POST", "/api/session"),
        ("PUT", f"/api/session/{SESSION_ID}/stop"),
    ]


async def test_an_async_session_is_opened_then_stopped(hyperbrowser: _Hyperbrowser) -> None:
    async with await _provider(hyperbrowser).aopen(BrowserRequest(timeout=60)) as browser:
        assert await browser.status() == "running"

    assert [(method, path) for method, path, _ in hyperbrowser.calls] == [
        ("POST", "/api/session"),
        ("GET", f"/api/session/{SESSION_ID}"),
        ("PUT", f"/api/session/{SESSION_ID}/stop"),
    ]


def test_a_response_without_a_ws_endpoint_releases_the_session_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    calls: list[tuple[str, str]] = []
    opened_without = {key: value for key, value in OPENED.items() if key != "wsEndpoint"}

    def missing(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json=opened_without)
        return httpx.Response(200, json={"success": True})

    with pytest.raises(ProviderError):
        HyperbrowserBrowserProvider(transport=httpx.MockTransport(missing)).open(BrowserRequest())

    assert calls == [("POST", "/api/session"), ("PUT", f"/api/session/{SESSION_ID}/stop")]


@pytest.mark.parametrize(
    ("reported", "state"),
    [("active", "running"), ("closed", "released"), ("error", "failed"), ("close-error", "failed")],
)
def test_status_becomes_the_normalized_state(monkeypatch: pytest.MonkeyPatch, reported: str, state: str) -> None:
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    assert _provider(_Hyperbrowser(reported)).status(SESSION_ID) == state


def test_an_unrecognized_status_raises_rather_than_passing_for_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    with pytest.raises(ProviderError, match="unrecognized session state"):
        _provider(_Hyperbrowser("paused")).status(SESSION_ID)


def test_stopping_a_session_that_already_closed_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    api = _Hyperbrowser("closed", release_status=404, release_body={"message": "Session not found"})

    _provider(api).release(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["PUT", "GET"]


def test_a_stop_that_answers_200_without_success_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 alone is not a stop: Hyperbrowser says whether it stopped the session in the body."""
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)

    with pytest.raises(ProviderError, match="success=False"):
        _provider(_Hyperbrowser(release_body={"success": False})).release(SESSION_ID)


def test_a_failed_stop_of_a_session_hyperbrowser_reports_as_close_error_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``close-error`` is a stop that went wrong: the session may still bill, so it is not treated as over."""
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)
    api = _Hyperbrowser("close-error", release_status=500, release_body={"message": "stop failed"})

    with pytest.raises(ProviderError, match="HTTP 500"):
        _provider(api).release(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["PUT", "GET"]


def test_a_refused_stop_of_a_live_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYPERBROWSER_API_KEY", KEY)

    with pytest.raises(ProviderError, match="HTTP 500"):
        _provider(_Hyperbrowser(release_status=500, release_body={"message": "boom"})).release(SESSION_ID)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("HYPERBROWSER_API_KEY"), reason="HYPERBROWSER_API_KEY not set")
def test_live_session_opens_reports_and_releases() -> None:
    provider = HyperbrowserBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(provider, timeout=60) as browser:
            assert browser.cdp_url.startswith("wss://")
            assert browser.status() == "running"
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("HYPERBROWSER_API_KEY"), reason="HYPERBROWSER_API_KEY not set")
async def test_live_async_session_opens_and_releases() -> None:
    provider = HyperbrowserBrowserProvider()
    async with AsyncWebRouter() as router:
        async with await router.open_browser(provider, timeout=60) as browser:
            assert await browser.status() == "running"
        assert browser.released

        assert await astate_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("HYPERBROWSER_API_KEY"), reason="HYPERBROWSER_API_KEY not set")
def test_live_session_opens_with_stealth_captchas_and_a_proxy_country() -> None:
    """The check that confirms the doc-derived mappings against a real account.

    Asserts the session Hyperbrowser actually launched, through ``launchState``
    in ``raw``, matches what was asked for, and separately records -- without
    failing the test over a bot check that stealth may or may not beat -- how
    far https://cookiesfordays.com/halloween-sugar-cookies gets past its
    "Just a moment..." challenge page.
    """
    provider = HyperbrowserBrowserProvider()
    with (
        WebRouter() as router,
        router.open_browser(provider, timeout=60, stealth="low", solve_captchas=True, proxy_country="US") as browser,
    ):
        launch_state = browser.session.raw["launchState"]
        assert launch_state["useStealth"] is True
        assert launch_state["useProxy"] is True
        assert launch_state["solveCaptchas"] is True

        page = browser.goto("https://cookiesfordays.com/halloween-sugar-cookies")
        print(f"cookiesfordays title after stealth='low': {page.title!r}")
        assert page.title, "the page must load something, whichever side of the challenge it landed on"

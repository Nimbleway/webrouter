"""Steel browser provider tests.

The mapping here is derived from Steel's documentation, not from a live call --
no account was available during development. ``timeout`` and ``idle_timeout``
come from the steel-python SDK's generated types. ``solve_captchas`` (->
``solveCaptcha``) and ``proxy_country`` (-> ``useProxy.geolocation.country``,
upper-case) are doc-derived from
https://docs.steel.dev/overview/sessions-api/configuration and
https://docs.steel.dev/overview/stealth/proxies (fetched 2026-09-24) and are
**unverified against a live call** -- the integration test below is the check
that confirms them. ``stealth`` is not mapped: Steel's
only stealth-adjacent field, ``stealthConfig``, tunes CAPTCHA auto-solving and
fingerprint behaviour Steel already applies to every session by default, with
no on/off or tiered switch to map a ``StealthLevel`` onto (same configuration
page, 2026-09-24). The recorded payloads below are hand-written from that
documentation. See CONTRIBUTING.md on verifying against the live API.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.providers._browser_live import astate_after_release, state_after_release
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import BROWSER_TIMEOUT
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.steel import SteelBrowserProvider
from web_router.types import BrowserRequest

KEY = "test-key"
SESSION_ID = "123e4567-e89b-12d3-a456-426614174000"

OPENED: dict[str, Any] = {
    "id": SESSION_ID,
    "createdAt": "2026-09-23T10:00:00Z",
    "status": "live",
    "timeout": 600000,
    "duration": 0,
    "creditsUsed": 0,
    "proxyBytesUsed": 0,
    "eventCount": 0,
    "browserMode": "standard",
    "websocketUrl": f"wss://steel-internal.example/{SESSION_ID}",
    "sessionViewerUrl": f"https://app.steel.dev/sessions/{SESSION_ID}",
    "debugUrl": f"https://api.steel.dev/v1/sessions/{SESSION_ID}/player",
    "dimensions": {"width": 1280, "height": 800},
}


class _Steel:
    """A recorded Steel API: answers each call from the payloads above and remembers what it was sent."""

    def __init__(self, reported: dict[str, Any] | None = None, release_status: int = 200) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.headers: list[httpx.Headers] = []
        self.reported = reported or {"status": "live", "releaseReason": None}
        self.release_status = release_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        self.headers.append(request.headers)
        if request.url.path.endswith("/release"):
            return httpx.Response(self.release_status, json={**OPENED, "status": "released"})
        if request.method == "GET":
            return httpx.Response(200, json={**OPENED, **self.reported})
        return httpx.Response(200, json=OPENED)


@pytest.fixture
def steel(monkeypatch: pytest.MonkeyPatch) -> _Steel:
    monkeypatch.setenv("STEEL_API_KEY", KEY)
    return _Steel()


def _provider(api: _Steel) -> SteelBrowserProvider:
    return SteelBrowserProvider(transport=httpx.MockTransport(api))


def test_the_key_is_sent_in_steels_own_header(steel: _Steel) -> None:
    with _provider(steel).open(BrowserRequest()):
        pass

    assert steel.headers[0]["steel-api-key"] == KEY
    assert "authorization" not in steel.headers[0]


def test_the_release_call_carries_the_key_too(steel: _Steel) -> None:
    with _provider(steel).open(BrowserRequest(timeout=60)):
        pass

    assert steel.calls[-1][1] == f"/v1/sessions/{SESSION_ID}/release"
    assert steel.headers[-1]["steel-api-key"] == KEY


def test_seconds_become_the_milliseconds_steel_takes(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(timeout=90, idle_timeout=15.5))
    assert payload == {"timeout": 90000, "inactivityTimeout": 15500}


def test_a_lifetime_under_a_millisecond_is_never_sent_as_zero(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(timeout=0.0005, idle_timeout=0.0001))
    assert payload == {"timeout": 1, "inactivityTimeout": 1}


def test_an_unset_idle_timeout_is_not_sent_and_the_lifetime_default_is(steel: _Steel) -> None:
    """Steel's own inactivity limit is off unless asked; ours must not switch it on."""
    with _provider(steel).open(BrowserRequest()):
        pass

    assert steel.calls[0] == ("POST", "/v1/sessions", {"timeout": round(BROWSER_TIMEOUT * 1000)})


def test_the_default_lifetime_reaches_the_wire_as_whole_milliseconds(steel: _Steel) -> None:
    """BROWSER_TIMEOUT is a float of seconds; Steel takes an integer count of milliseconds."""
    with _provider(steel).open(BrowserRequest()):
        pass

    sent = steel.calls[0][2]
    assert sent == {"timeout": 600000}
    assert type(sent["timeout"]) is int


def test_a_lifetime_in_provider_options_collides_with_the_default_and_raises(steel: _Steel) -> None:
    """With no ``timeout=``, the lifetime is still sent from BROWSER_TIMEOUT, so the native field would fight it."""
    with pytest.raises(ConfigurationError, match="pass it as timeout="):
        _provider(steel).open(BrowserRequest(provider_options={"timeout": 5}))
    assert steel.calls == [], "refused before a session is opened and billed"


def test_provider_options_reach_steel_untranslated(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(timeout=60, provider_options={"useProxy": True}))
    assert payload == {"timeout": 60000, "useProxy": True}


@pytest.mark.parametrize(
    "option",
    [
        {"stealth": "low"},
        {"profile": "p-1"},
        {"persist_profile": True},
        {"viewport": {"width": 800, "height": 600}},
        {"headless": True},
    ],
)
def test_options_no_live_call_confirmed_are_rejected_not_dropped(steel: _Steel, option: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedParameterError):
        _provider(steel).open(BrowserRequest(**option))
    assert steel.calls == [], "rejected before a session is opened and billed"


def test_stealth_is_not_mapped_at_all(steel: _Steel) -> None:
    """Steel's only stealth-adjacent field is `stealthConfig`, and it has no on/off or tiered switch to map."""
    assert "stealth" not in SteelBrowserProvider.supported_parameters
    assert SteelBrowserProvider.stealth_levels == frozenset()


def test_solve_captchas_is_sent_as_solve_captcha_verbatim(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(solve_captchas=True))
    assert payload == {"solveCaptcha": True}


def test_solve_captchas_false_is_sent_explicitly_not_dropped(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(solve_captchas=False))
    assert payload == {"solveCaptcha": False}


def test_proxy_country_becomes_the_useproxy_geolocation_object(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(proxy_country="us"))
    assert payload == {"useProxy": {"geolocation": {"country": "US"}}}


def test_proxy_country_is_sent_upper_case_regardless_of_caller_casing(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(proxy_country="de"))
    assert payload["useProxy"]["geolocation"]["country"] == "DE"


def test_steel_has_no_enumerated_proxy_country_list(steel: _Steel) -> None:
    """Steel's docs say 'over 200 countries via their two-letter Alpha-2 codes' with no list to enforce."""
    assert SteelBrowserProvider.proxy_countries is None
    payload = _provider(steel)._build_payload(BrowserRequest(proxy_country="ZZ"))
    assert payload == {"useProxy": {"geolocation": {"country": "ZZ"}}}


def test_solve_captchas_and_proxy_country_combine_with_the_timeouts(steel: _Steel) -> None:
    payload = _provider(steel)._build_payload(BrowserRequest(timeout=60, solve_captchas=True, proxy_country="US"))
    assert payload == {
        "timeout": 60000,
        "solveCaptcha": True,
        "useProxy": {"geolocation": {"country": "US"}},
    }


def test_provider_options_setting_the_proxy_field_raises_rather_than_merging(steel: _Steel) -> None:
    with pytest.raises(ConfigurationError, match="useProxy"):
        _provider(steel).open(BrowserRequest(proxy_country="US", provider_options={"useProxy": True}))
    assert steel.calls == [], "refused before a session is opened and billed"


def test_provider_options_setting_the_captcha_field_raises_rather_than_merging(steel: _Steel) -> None:
    with pytest.raises(ConfigurationError, match="solveCaptcha"):
        _provider(steel).open(BrowserRequest(solve_captchas=True, provider_options={"solveCaptcha": False}))
    assert steel.calls == [], "refused before a session is opened and billed"


def test_the_cdp_url_is_steels_connect_endpoint_not_the_reported_websocket(steel: _Steel) -> None:
    with _provider(steel).open(BrowserRequest()) as browser:
        cdp_url = urlsplit(browser.cdp_url)

        assert (cdp_url.scheme, cdp_url.netloc) == ("wss", "connect.steel.dev")
        assert parse_qs(cdp_url.query) == {"apiKey": [KEY], "sessionId": [SESSION_ID]}
        assert KEY not in repr(browser)
        assert KEY not in repr(browser.session)


def test_the_session_is_normalized_without_the_control_url(steel: _Steel) -> None:
    with _provider(steel).open(BrowserRequest()) as browser:
        session = browser.session

    assert session.provider == "steel"
    assert session.session_id == SESSION_ID
    assert session.live_view_url == OPENED["sessionViewerUrl"]
    assert session.expires_at is None, "Steel reports no expiry, and one is not worked out for it"
    assert session.raw == OPENED
    assert OPENED["debugUrl"] not in session.model_dump_json(), "debugUrl grants control, so it stays in raw"


def test_a_session_is_opened_then_released_by_its_id(steel: _Steel) -> None:
    with _provider(steel).open(BrowserRequest(timeout=60)):
        pass

    assert [(method, path) for method, path, _ in steel.calls] == [
        ("POST", "/v1/sessions"),
        ("POST", f"/v1/sessions/{SESSION_ID}/release"),
    ], "one session released, never the account-wide /v1/sessions/release"


async def test_an_async_session_is_opened_then_released(steel: _Steel) -> None:
    async with await _provider(steel).aopen(BrowserRequest(timeout=60)) as browser:
        assert await browser.status() == "running"

    assert [(method, path) for method, path, _ in steel.calls] == [
        ("POST", "/v1/sessions"),
        ("GET", f"/v1/sessions/{SESSION_ID}"),
        ("POST", f"/v1/sessions/{SESSION_ID}/release"),
    ]


@pytest.mark.parametrize(
    ("reported", "state"),
    [
        ({"status": "live", "releaseReason": None}, "running"),
        ({"status": "released", "releaseReason": "user_requested"}, "released"),
        ({"status": "released", "releaseReason": "browser_closed"}, "released"),
        ({"status": "released", "releaseReason": "timeout"}, "expired"),
        ({"status": "released", "releaseReason": "inactivity_timeout"}, "expired"),
        ({"status": "released", "releaseReason": "creation_timeout"}, "failed"),
        ({"status": "released", "releaseReason": "startup_failed"}, "failed"),
        ({"status": "released", "releaseReason": "browser_crashed"}, "failed"),
        ({"status": "released", "releaseReason": None}, "released"),
        ({"status": "failed", "releaseReason": None}, "failed"),
    ],
)
def test_status_and_release_reason_become_the_normalized_state(
    monkeypatch: pytest.MonkeyPatch, reported: dict[str, Any], state: str
) -> None:
    monkeypatch.setenv("STEEL_API_KEY", KEY)
    assert _provider(_Steel(reported)).status(SESSION_ID) == state


@pytest.mark.parametrize(
    "reported",
    [{"status": "paused", "releaseReason": None}, {"status": "released", "releaseReason": "evicted"}],
    ids=["status", "release-reason"],
)
def test_an_unrecognized_state_raises_rather_than_passing_for_running(
    monkeypatch: pytest.MonkeyPatch, reported: dict[str, Any]
) -> None:
    monkeypatch.setenv("STEEL_API_KEY", KEY)
    with pytest.raises(ProviderError, match="unrecognized session state"):
        _provider(_Steel(reported)).status(SESSION_ID)


def test_releasing_a_session_that_already_timed_out_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEEL_API_KEY", KEY)
    api = _Steel({"status": "released", "releaseReason": "timeout"}, release_status=404)

    _provider(api).release(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["POST", "GET"]


def test_a_refused_release_of_a_live_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEEL_API_KEY", KEY)

    with pytest.raises(ProviderError, match="HTTP 500"):
        _provider(_Steel(release_status=500)).release(SESSION_ID)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("STEEL_API_KEY"), reason="STEEL_API_KEY not set")
def test_live_session_opens_reports_and_releases() -> None:
    provider = SteelBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(provider, timeout=60) as browser:
            assert browser.cdp_url.startswith("wss://")
            assert browser.status() == "running"
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("STEEL_API_KEY"), reason="STEEL_API_KEY not set")
def test_live_session_with_solve_captchas_and_proxy_country_opens_and_releases() -> None:
    """Steel does not map `stealth`, so there is no cookiesfordays check here -- record the run under this instead.

    Proves the doc-derived `solveCaptcha` and `useProxy.geolocation.country`
    shape is accepted by a live call.
    """
    provider = SteelBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(provider, timeout=60, solve_captchas=True, proxy_country="US") as browser:
            assert browser.cdp_url.startswith("wss://")
            assert browser.status() == "running"
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("STEEL_API_KEY"), reason="STEEL_API_KEY not set")
async def test_live_async_session_opens_and_releases() -> None:
    provider = SteelBrowserProvider()
    async with AsyncWebRouter() as router:
        async with await router.open_browser(provider, timeout=60) as browser:
            assert await browser.status() == "running"
        assert browser.released

        assert await astate_after_release(provider, browser.session_id) == "released"

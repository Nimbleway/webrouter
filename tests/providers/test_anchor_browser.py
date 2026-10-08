"""Anchor browser provider tests.

The mapping here is derived from Anchor's published OpenAPI spec
(docs.anchorbrowser.io/openapi.yaml) and its guides, not from a live call -- no
account was available during development. The recorded payloads below are
hand-written from that spec, and ``timeout`` and ``idle_timeout`` are mapped
because nothing else could be confirmed at the time.

``stealth``, ``solve_captchas`` and ``proxy_country`` are **also doc-derived
and unverified against a live call**, read from
https://docs.anchorbrowser.io/essentials/stealth and
https://docs.anchorbrowser.io/advanced/proxy on 2026-09-24.
``test_live_session_serves_stealth_solve_captchas_and_proxy_country`` below is
the check that confirms them against a real account -- see AGENTS.md on
verifying against the live service. The spec types a session's ``status`` as a bare string, so the
status table is built from the values its examples and webhook events show.
The integration tests below are the checks that matter; run them once a key is
available. See CONTRIBUTING.md on verifying against the live API.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from tests.providers._browser_live import astate_after_release, state_after_release
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
)
from web_router.providers.anchor import AnchorBrowserProvider
from web_router.types import BrowserRequest

KEY = "test-key"
SESSION_ID = "9c0d1e2f-3a4b-5c6d-7e8f-9a0b1c2d3e4f"
CDP_URL = f"wss://connect.anchorbrowser.io?apiKey={KEY}&sessionId={SESSION_ID}"

OPENED: dict[str, Any] = {
    "data": {
        "id": SESSION_ID,
        "cdp_url": CDP_URL,
        "live_view_url": f"https://live.anchorbrowser.io?sessionId={SESSION_ID}",
    }
}


def _reported(status: str) -> dict[str, Any]:
    return {
        "data": {
            "session_id": SESSION_ID,
            "team_id": "team-1",
            "duration": 42,
            "status": status,
            "credits_used": 0.5,
            "configuration": {},
            "proxy_bytes": 0,
            "tags": [],
            "created_at": "2026-09-23T10:00:00Z",
        }
    }


class _Anchor:
    """A recorded Anchor API: answers each call from the payloads above and remembers what it was sent."""

    def __init__(self, status: str = "running", release_status: int = 200) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.headers: list[httpx.Headers] = []
        self.status = status
        self.release_status = release_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        self.headers.append(request.headers)
        if request.method == "DELETE":
            return httpx.Response(self.release_status, json={"data": {"status": "success"}})
        if request.method == "GET":
            return httpx.Response(200, json=_reported(self.status))
        return httpx.Response(200, json=OPENED)


@pytest.fixture
def anchor(monkeypatch: pytest.MonkeyPatch) -> _Anchor:
    monkeypatch.setenv("ANCHOR_API_KEY", KEY)
    return _Anchor()


def _provider(api: _Anchor) -> AnchorBrowserProvider:
    return AnchorBrowserProvider(transport=httpx.MockTransport(api))


def test_the_key_is_sent_in_anchors_own_header_on_every_call(anchor: _Anchor) -> None:
    with _provider(anchor).open(BrowserRequest()):
        pass

    for headers in anchor.headers:
        assert headers["anchor-api-key"] == KEY
        assert "authorization" not in headers


def test_seconds_become_the_whole_minutes_anchor_takes_rounded_up(anchor: _Anchor) -> None:
    """Rounded up, so a session is never ended sooner than the caller asked."""
    payload = _provider(anchor)._build_payload(BrowserRequest(timeout=600, idle_timeout=90))
    assert payload == {"session": {"timeout": {"max_duration": 10, "idle_timeout": 2}}}


def test_an_unset_idle_timeout_is_not_sent_and_the_lifetime_default_is(anchor: _Anchor) -> None:
    """Anchor's own five-minute idle limit stays Anchor's; the router's ten-minute lifetime is sent."""
    with _provider(anchor).open(BrowserRequest()):
        pass

    sent = anchor.calls[0]
    assert sent == ("POST", "/v1/sessions", {"session": {"timeout": {"max_duration": 10}}})
    assert type(sent[2]["session"]["timeout"]["max_duration"]) is int  # type: ignore[index]


def test_headful_and_recording_stay_anchors_defaults(anchor: _Anchor) -> None:
    """Both are on by default at Anchor; unset means unset, so nothing is sent to switch them off."""
    payload = _provider(anchor)._build_payload(BrowserRequest(timeout=60))
    assert "browser" not in payload
    assert set(payload["session"]) == {"timeout"}


def test_provider_options_reach_anchor_untranslated(anchor: _Anchor) -> None:
    options = {"browser": {"headless": {"active": True}}}
    payload = _provider(anchor)._build_payload(BrowserRequest(timeout=60, provider_options=options))
    assert payload == {"session": {"timeout": {"max_duration": 1}}, **options}


def test_recording_off_in_provider_options_keeps_the_mapped_timeouts(anchor: _Anchor) -> None:
    options = {"session": {"recording": {"active": False}}}
    payload = _provider(anchor)._build_payload(BrowserRequest(timeout=600, idle_timeout=60, provider_options=options))

    assert payload == {
        "session": {"recording": {"active": False}, "timeout": {"max_duration": 10, "idle_timeout": 1}},
    }
    assert options == {"session": {"recording": {"active": False}}}, "the caller's options are not mutated"


def test_the_default_lifetime_survives_session_options(anchor: _Anchor) -> None:
    with _provider(anchor).open(BrowserRequest(provider_options={"session": {"recording": {"active": False}}})):
        pass

    assert anchor.calls[0][2] == {"session": {"recording": {"active": False}, "timeout": {"max_duration": 10}}}


@pytest.mark.parametrize(
    ("request_fields", "theirs"),
    [({"timeout": 60}, {"max_duration": 5}), ({"idle_timeout": 60}, {"idle_timeout": 5})],
    ids=["max_duration", "idle_timeout"],
)
def test_a_timeout_set_both_ways_raises_rather_than_one_side_winning(
    anchor: _Anchor, request_fields: dict[str, Any], theirs: dict[str, Any]
) -> None:
    options = {"session": {"timeout": theirs}}
    with pytest.raises(ConfigurationError, match="set it in one place"):
        _provider(anchor).open(BrowserRequest(**request_fields, provider_options=options))
    assert anchor.calls == [], "refused before a session is opened and billed"


@pytest.mark.parametrize(
    "option",
    [
        {"profile": "p-1"},
        {"persist_profile": True},
        {"viewport": {"width": 800, "height": 600}},
        {"headless": True},
    ],
)
def test_options_no_live_call_confirmed_are_rejected_not_dropped(anchor: _Anchor, option: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedParameterError):
        _provider(anchor).open(BrowserRequest(**option))
    assert anchor.calls == [], "rejected before a session is opened and billed"


# -- stealth, solve_captchas, proxy_country (doc-derived, unverified) --------


@pytest.mark.parametrize(
    ("level", "extra_stealth"),
    [("off", False), ("low", True), ("high", True)],
)
def test_stealth_level_maps_to_anchors_one_extra_stealth_tier(anchor: _Anchor, level: str, extra_stealth: bool) -> None:
    """Anchor has one stealth tier, so `low` and `high` both turn the same field on."""
    kwargs: dict[str, Any] = {"stealth": level}
    if level != "off":
        kwargs["proxy_country"] = "US"
    payload = _provider(anchor)._build_payload(BrowserRequest(**kwargs))
    assert payload["browser"]["extra_stealth"]["active"] is extra_stealth


def test_solve_captchas_false_is_sent_explicitly_not_dropped(anchor: _Anchor) -> None:
    payload = _provider(anchor)._build_payload(BrowserRequest(solve_captchas=False))
    assert payload == {"browser": {"captcha_solver": {"active": False}}}


def test_proxy_country_turns_the_proxy_on_and_is_sent_lower_case(anchor: _Anchor) -> None:
    payload = _provider(anchor)._build_payload(BrowserRequest(proxy_country="US"))
    assert payload == {"session": {"proxy": {"active": True, "country_code": "us"}}}


def test_a_country_outside_anchors_published_list_is_refused_before_a_session_opens(anchor: _Anchor) -> None:
    with pytest.raises(UnsupportedParameterError, match="does not support proxy_country='ZZ'") as caught:
        _provider(anchor).open(BrowserRequest(proxy_country="ZZ"))

    assert (caught.value.parameters, caught.value.values) == (["proxy_country"], {"proxy_country": "ZZ"})
    assert anchor.calls == [], "refused before a session is opened and billed"


def test_stealth_and_proxy_country_together_produce_disjoint_session_leaves(anchor: _Anchor) -> None:
    """session.timeout, session.proxy and browser.extra_stealth never collide as table rows."""
    payload = _provider(anchor)._build_payload(
        BrowserRequest(timeout=60, stealth="low", proxy_country="US", solve_captchas=True)
    )
    assert payload == {
        "session": {"timeout": {"max_duration": 1}, "proxy": {"active": True, "country_code": "us"}},
        "browser": {"extra_stealth": {"active": True}, "captcha_solver": {"active": True}},
    }


@pytest.mark.parametrize("level", ["low", "high"])
def test_stealth_without_a_proxy_country_is_refused_before_a_session_opens(anchor: _Anchor, level: str) -> None:
    """Anchor silently drops extra stealth without an active proxy; refuse rather than serve less than asked."""
    with pytest.raises(MissingParameterError, match="proxy_country") as caught:
        _provider(anchor).open(BrowserRequest(stealth=level))

    assert caught.value.parameters == ["proxy_country"]
    assert anchor.calls == [], "no session -- and so no payload -- is built for an unmet co-requirement"


def test_stealth_off_needs_no_proxy_country(anchor: _Anchor) -> None:
    with _provider(anchor).open(BrowserRequest(stealth="off")):
        pass

    assert anchor.calls[0][2] == {
        "session": {"timeout": {"max_duration": 10}},
        "browser": {"extra_stealth": {"active": False}},
    }


@pytest.mark.parametrize(
    ("request_fields", "options"),
    [
        ({"stealth": "off"}, {"browser": {"extra_stealth": {"active": True}}}),
        ({"solve_captchas": True}, {"browser": {"captcha_solver": {"active": False}}}),
        ({"proxy_country": "US"}, {"session": {"proxy": {"active": False}}}),
    ],
    ids=["stealth", "solve_captchas", "proxy_country"],
)
def test_provider_options_setting_any_of_the_new_produced_fields_raises(
    anchor: _Anchor, request_fields: dict[str, Any], options: dict[str, Any]
) -> None:
    extra = {"proxy_country": "US"} if request_fields.get("stealth") == "off" else {}
    with pytest.raises(ConfigurationError, match="set it in one place"):
        _provider(anchor).open(BrowserRequest(**request_fields, **extra, provider_options=options))
    assert anchor.calls == [], "refused before a session is opened and billed"


def test_the_session_is_normalized_with_the_cdp_url_secret_and_no_control_url(anchor: _Anchor) -> None:
    with _provider(anchor).open(BrowserRequest()) as browser:
        session = browser.session
        assert browser.cdp_url == CDP_URL
        assert KEY not in repr(browser)
        assert KEY not in repr(session)
        assert KEY not in session.model_dump_json()

    assert session.provider == "anchor"
    assert session.session_id == SESSION_ID
    assert session.live_view_url is None, "Anchor's live view is interactive by default, so it stays in raw"
    assert session.expires_at is None, "Anchor reports no expiry, and one is not worked out for it"
    assert session.raw == OPENED
    assert OPENED["data"]["live_view_url"] not in session.model_dump_json()


def test_a_session_is_opened_then_deleted_by_its_id(anchor: _Anchor) -> None:
    with _provider(anchor).open(BrowserRequest(timeout=60)):
        pass

    assert [(method, path) for method, path, _ in anchor.calls] == [
        ("POST", "/v1/sessions"),
        ("DELETE", f"/v1/sessions/{SESSION_ID}"),
    ], "one session ended, never the account-wide /v1/sessions/all"


async def test_an_async_session_is_opened_then_released(anchor: _Anchor) -> None:
    async with await _provider(anchor).aopen(BrowserRequest(timeout=60)) as browser:
        assert await browser.status() == "running"

    assert [(method, path) for method, path, _ in anchor.calls] == [
        ("POST", "/v1/sessions"),
        ("GET", f"/v1/sessions/{SESSION_ID}"),
        ("DELETE", f"/v1/sessions/{SESSION_ID}"),
    ]


def test_an_answer_that_cannot_be_read_releases_the_session_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """The id sits under `data`; the orphaned session is still found and ended."""
    monkeypatch.setenv("ANCHOR_API_KEY", KEY)
    calls: list[tuple[str, str]] = []

    def api(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": SESSION_ID}})
        return httpx.Response(200, json={"data": {"status": "success"}})

    with pytest.raises(ProviderError):
        AnchorBrowserProvider(transport=httpx.MockTransport(api)).open(BrowserRequest())

    assert calls[-1] == ("DELETE", f"/v1/sessions/{SESSION_ID}")


@pytest.mark.parametrize(
    ("status", "state"),
    [("running", "running"), ("completed", "released"), ("failed", "failed")],
)
def test_status_becomes_the_normalized_state(monkeypatch: pytest.MonkeyPatch, status: str, state: str) -> None:
    monkeypatch.setenv("ANCHOR_API_KEY", KEY)
    assert _provider(_Anchor(status)).status(SESSION_ID) == state


def test_an_unrecognized_status_raises_rather_than_passing_for_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANCHOR_API_KEY", KEY)
    with pytest.raises(ProviderError, match="unrecognized session state"):
        _provider(_Anchor("paused")).status(SESSION_ID)


def test_releasing_a_session_that_already_ended_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANCHOR_API_KEY", KEY)
    api = _Anchor("completed", release_status=401)

    _provider(api).release(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["DELETE", "GET"]


def test_a_refused_release_of_a_live_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANCHOR_API_KEY", KEY)

    with pytest.raises(ProviderError, match="HTTP 500"):
        _provider(_Anchor(release_status=500)).release(SESSION_ID)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANCHOR_API_KEY"), reason="ANCHOR_API_KEY not set")
def test_live_session_opens_reports_and_releases() -> None:
    provider = AnchorBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(provider, timeout=60, idle_timeout=60) as browser:
            assert browser.cdp_url.startswith("wss://")
            assert browser.status() == "running"
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANCHOR_API_KEY"), reason="ANCHOR_API_KEY not set")
async def test_live_async_session_opens_and_releases() -> None:
    provider = AnchorBrowserProvider()
    async with AsyncWebRouter() as router:
        async with await router.open_browser(provider, timeout=60) as browser:
            assert await browser.status() == "running"
        assert browser.released

        assert await astate_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANCHOR_API_KEY"), reason="ANCHOR_API_KEY not set")
def test_live_session_serves_stealth_solve_captchas_and_proxy_country() -> None:
    """The check that confirms the doc-derived mappings: stealth, solve_captchas and proxy_country, live.

    Opens with all three, reads the session back from Anchor, then loads a
    real bot-check page and records whether the title moves past "Just a
    moment..." -- the challenge cookiesfordays.com/halloween-sugar-cookies
    shows a browser it has not been fooled by.
    """
    provider = AnchorBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(
            provider,
            stealth="low",
            solve_captchas=True,
            proxy_country="US",
            timeout=60,
        ) as browser:
            assert browser.status() == "running"
            page = browser.goto("https://cookiesfordays.com/halloween-sugar-cookies")
            assert page.title != "Just a moment...", "stealth did not get past the challenge page"
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"

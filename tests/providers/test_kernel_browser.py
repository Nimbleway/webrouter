"""Kernel browser provider tests.

The recorded payloads below are the shape live calls returned on 2026-10-20,
with the connection URLs replaced. Every mapped option was checked live that
day: ``idle_timeout`` (``timeout_seconds``; 400 below 10), ``stealth``
(``stealth: true`` also switched ``proxy.mode`` from ``direct`` to
``default``), ``headless``, ``viewport`` (echoed back with a refresh rate
Kernel picked), and ``profile`` / ``persist_profile`` (``profile.id`` and
``profile.save_changes``; a profile's name sent as its id answered
``profile not found``). A deleted session answered ``GET`` and ``DELETE``
with 404 ``not_found``. The integration tests at the bottom repeat the checks
that matter.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import pytest

from tests.providers._browser_live import astate_after_release, state_after_release
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import KERNEL_BASE_URL
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
)
from web_router.providers.kernel import KernelBrowserProvider
from web_router.types import BrowserRequest

KEY = "test-key"
SESSION_ID = "m59kjkqu0ppn5ox62gktms2o"
CDP_URL = f"wss://proxy.iad-example.onkernel.com:8443/browser/cdp?jwt=session-token-{SESSION_ID}"
LIVE_VIEW_URL = "https://api.onkernel.com/browser/live/live-token"

OPENED: dict[str, Any] = {
    "base_url": "https://proxy.iad-example.onkernel.com:8443/browser/kernel",
    "browser_live_view_url": LIVE_VIEW_URL,
    "cdp_ws_url": CDP_URL,
    "created_at": "2026-10-20T07:40:54.212814001Z",
    "gpu": False,
    "headless": False,
    "kiosk_mode": False,
    "memory": "8GiB",
    "proxy": {"mode": "direct"},
    "region": "us-east",
    "session_id": SESSION_ID,
    "stealth": False,
    "timeout_seconds": 60,
    "webdriver_ws_url": "wss://proxy.iad-example.onkernel.com:8443/browser/webdriver?jwt=session-token",
}

NOT_FOUND = {"code": "not_found", "message": f"browser session '{SESSION_ID}' not found"}


class _Kernel:
    """A recorded Kernel API: answers each call as the live one did and remembers what it was sent."""

    def __init__(self, *, gone: bool = False, gone_code: str = "not_found", release_status: int = 204) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.headers: list[httpx.Headers] = []
        self.gone = gone
        self.gone_code = gone_code
        self.release_status = release_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        self.headers.append(request.headers)
        if self.gone and request.method in {"GET", "DELETE"}:
            return httpx.Response(404, json={**NOT_FOUND, "code": self.gone_code})
        if request.method == "DELETE":
            return httpx.Response(self.release_status)
        return httpx.Response(200, json=OPENED)


@pytest.fixture
def kernel(monkeypatch: pytest.MonkeyPatch) -> _Kernel:
    monkeypatch.setenv("KERNEL_API_KEY", KEY)
    return _Kernel()


def _provider(api: _Kernel) -> KernelBrowserProvider:
    return KernelBrowserProvider(transport=httpx.MockTransport(api))


def test_the_key_is_sent_as_a_bearer_token_on_every_call(kernel: _Kernel) -> None:
    with _provider(kernel).open(BrowserRequest()) as browser:
        browser.status()

    assert len(kernel.headers) == 3
    for headers in kernel.headers:
        assert headers["authorization"] == f"Bearer {KEY}"


def test_no_lifetime_is_sent_because_kernel_has_only_an_idle_limit(kernel: _Kernel) -> None:
    """The ten-minute default reaches only a backend that maps `timeout`; Kernel keeps its own idle limit."""
    with _provider(kernel).open(BrowserRequest()):
        pass

    assert kernel.calls[0] == ("POST", "/browsers", {})


def test_a_lifetime_is_refused_rather_than_sent_as_the_idle_limit(kernel: _Kernel) -> None:
    with pytest.raises(UnsupportedParameterError, match="timeout"):
        _provider(kernel).open(BrowserRequest(timeout=600))
    assert kernel.calls == [], "refused before a session is opened and billed"


@pytest.mark.parametrize(("seconds", "sent"), [(30, 30), (90.2, 91)])
def test_idle_timeout_is_whole_seconds_rounded_up(kernel: _Kernel, seconds: float, sent: int) -> None:
    payload = _provider(kernel)._build_payload(BrowserRequest(idle_timeout=seconds))
    assert payload == {"timeout_seconds": sent}
    assert type(payload["timeout_seconds"]) is int


@pytest.mark.parametrize(("level", "stealth"), [("off", False), ("low", True), ("high", True)])
def test_stealth_level_maps_to_kernels_one_switch(kernel: _Kernel, level: str, stealth: bool) -> None:
    payload = _provider(kernel)._build_payload(BrowserRequest(stealth=level))
    assert payload == {"stealth": stealth}


def test_headless_viewport_and_profile_land_where_kernel_reads_them(kernel: _Kernel) -> None:
    payload = _provider(kernel)._build_payload(
        BrowserRequest(
            headless=True,
            viewport={"width": 1280, "height": 800},
            profile="by0bl9sbyi0i1526sw5r00ho",
            persist_profile=True,
        )
    )
    assert payload == {
        "headless": True,
        "viewport": {"width": 1280, "height": 800},
        "profile": {"id": "by0bl9sbyi0i1526sw5r00ho", "save_changes": True},
    }


@pytest.mark.parametrize("persist", [True, False])
def test_persist_profile_without_a_profile_is_refused_before_a_session_opens(kernel: _Kernel, persist: bool) -> None:
    with pytest.raises(MissingParameterError, match="profile") as caught:
        _provider(kernel).open(BrowserRequest(persist_profile=persist))

    assert caught.value.parameters == ["profile"]
    assert kernel.calls == []


@pytest.mark.parametrize(
    "option",
    [{"timeout": 60}, {"proxy_country": "US"}, {"solve_captchas": True}, {"solve_captchas": False}],
)
def test_options_kernel_cannot_honour_are_rejected_not_dropped(kernel: _Kernel, option: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedParameterError):
        _provider(kernel).open(BrowserRequest(**option))
    assert kernel.calls == [], "rejected before a session is opened and billed"


def test_provider_options_reach_kernel_beneath_the_mapped_fields(kernel: _Kernel) -> None:
    options = {"proxy": {"id": "proxy-1"}, "profile": {"name": "shared"}}
    payload = _provider(kernel)._build_payload(BrowserRequest(idle_timeout=60, provider_options=options))
    assert payload == {"timeout_seconds": 60, **options}


@pytest.mark.parametrize(
    ("request_fields", "options"),
    [
        ({"idle_timeout": 60}, {"timeout_seconds": 10}),
        ({"stealth": "off"}, {"stealth": True}),
        ({"profile": "p-1"}, {"profile": {"id": "p-2"}}),
        ({"viewport": {"width": 800, "height": 600}}, {"viewport": {"refresh_rate": 60}}),
    ],
    ids=["idle_timeout", "stealth", "profile", "viewport"],
)
def test_a_field_set_both_ways_raises_rather_than_one_side_winning(
    kernel: _Kernel, request_fields: dict[str, Any], options: dict[str, Any]
) -> None:
    with pytest.raises(ConfigurationError, match="set it in one place"):
        _provider(kernel).open(BrowserRequest(**request_fields, provider_options=options))
    assert kernel.calls == []


def test_the_session_is_normalized_with_the_cdp_url_secret_and_no_live_view(kernel: _Kernel) -> None:
    with _provider(kernel).open(BrowserRequest()) as browser:
        session = browser.session
        assert browser.cdp_url == CDP_URL
        assert "session-token" not in repr(browser)
        assert "session-token" not in repr(session)
        assert "session-token" not in session.model_dump_json()

    assert session.provider == "kernel"
    assert session.session_id == SESSION_ID
    assert session.live_view_url is None, "Kernel's live view is interactive, so it stays in raw"
    assert session.expires_at is None, "Kernel reports no expiry, and one is not worked out for it"
    assert session.raw == OPENED
    assert LIVE_VIEW_URL not in session.model_dump_json()


def test_a_session_is_opened_then_deleted_by_its_id(kernel: _Kernel) -> None:
    with _provider(kernel).open(BrowserRequest(idle_timeout=60)):
        pass

    assert [(method, path) for method, path, _ in kernel.calls] == [
        ("POST", "/browsers"),
        ("DELETE", f"/browsers/{SESSION_ID}"),
    ]


async def test_an_async_session_is_opened_then_released(kernel: _Kernel) -> None:
    async with await _provider(kernel).aopen(BrowserRequest(idle_timeout=60)) as browser:
        assert await browser.status() == "running"

    assert [(method, path) for method, path, _ in kernel.calls] == [
        ("POST", "/browsers"),
        ("GET", f"/browsers/{SESSION_ID}"),
        ("DELETE", f"/browsers/{SESSION_ID}"),
    ]


def test_an_answer_that_cannot_be_read_releases_the_session_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """The id is `session_id`, not `id`; the orphaned session is still found and deleted."""
    monkeypatch.setenv("KERNEL_API_KEY", KEY)
    calls: list[tuple[str, str]] = []

    def api(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"session_id": SESSION_ID})
        return httpx.Response(204)

    with pytest.raises(ProviderError):
        KernelBrowserProvider(transport=httpx.MockTransport(api)).open(BrowserRequest())

    assert calls[-1] == ("DELETE", f"/browsers/{SESSION_ID}")


def test_a_session_kernel_no_longer_has_reads_as_released(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_API_KEY", KEY)
    assert _provider(_Kernel(gone=True)).status(SESSION_ID) == "released"


def test_an_unrecognized_404_code_raises_rather_than_passing_for_released(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_API_KEY", KEY)
    with pytest.raises(ProviderError, match="unrecognized session state"):
        _provider(_Kernel(gone=True, gone_code="moved")).status(SESSION_ID)


def test_releasing_a_session_kernel_already_deleted_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An idle session Kernel deleted on its own answers DELETE with 404; what the caller wanted is true."""
    monkeypatch.setenv("KERNEL_API_KEY", KEY)
    api = _Kernel(gone=True)

    _provider(api).release(SESSION_ID)

    assert [method for method, _, _ in api.calls] == ["DELETE", "GET"]


def test_a_refused_release_of_a_live_session_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNEL_API_KEY", KEY)

    with pytest.raises(ProviderError, match="HTTP 500"):
        _provider(_Kernel(release_status=500)).release(SESSION_ID)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("KERNEL_API_KEY"), reason="KERNEL_API_KEY not set")
def test_live_session_opens_is_driven_and_releases() -> None:
    provider = KernelBrowserProvider()
    with WebRouter() as router:
        with router.open_browser(
            provider,
            idle_timeout=30,
            headless=True,
            stealth="low",
            viewport={"width": 1280, "height": 800},
        ) as browser:
            assert browser.cdp_url.startswith("wss://")
            assert browser.status() == "running"
            assert browser.session.raw["stealth"] is True
            assert browser.session.raw["viewport"]["width"] == 1280
            assert browser.goto("https://example.com").title == "Example Domain"
        assert browser.released

        assert state_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("KERNEL_API_KEY"), reason="KERNEL_API_KEY not set")
async def test_live_async_session_opens_and_releases() -> None:
    provider = KernelBrowserProvider()
    async with AsyncWebRouter() as router:
        async with await router.open_browser(provider, idle_timeout=30) as browser:
            assert await browser.status() == "running"
        assert browser.released

        assert await astate_after_release(provider, browser.session_id) == "released"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("KERNEL_API_KEY"), reason="KERNEL_API_KEY not set")
def test_live_session_starts_from_a_profile_by_its_id() -> None:
    """Creates a throwaway profile through Kernel's Profiles API, opens a session on it, and deletes both."""
    provider = KernelBrowserProvider()
    profiles = httpx.Client(
        base_url=KERNEL_BASE_URL, headers={"Authorization": f"Bearer {os.environ['KERNEL_API_KEY']}"}
    )
    profile = (
        profiles.post("/profiles", json={"name": f"web-router-test-{uuid.uuid4().hex[:8]}"}).raise_for_status().json()
    )
    try:
        with (
            WebRouter() as router,
            router.open_browser(
                provider, idle_timeout=10, headless=True, profile=profile["id"], persist_profile=True
            ) as browser,
        ):
            assert browser.session.raw["profile"]["id"] == profile["id"]
            # Sent nested as `profile.save_changes`, answered flat: Kernel echoes it top-level.
            assert browser.session.raw["profile_save_changes"] is True
    finally:
        profiles.delete(f"/profiles/{profile['id']}")
        profiles.close()

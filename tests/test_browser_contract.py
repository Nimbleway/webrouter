"""Guards on the browser session contract.

The fourth counterpart of test_contract.py, for the method that returns neither
an answer nor a job but a *resource*: a cloud browser that bills for every
minute it stays open. So beside the usual drift guards these pin the one
promise that matters most here -- a session that is opened is released, on
every way out of the code that holds it -- and the one that matters next: the
CDP URL is a credential, and nothing that prints a session prints it.

Everything runs against the fake in tests/conftest.py and a minimal
`HttpBrowserProvider` subclass; the registry-driven checks pick up each real
backend, `auto` among them. The last section holds `auto` to the rule that a
session once opened is paid for: it may pass a member over only before one
exists.
"""

from __future__ import annotations

import gc
import inspect
import threading
from types import MappingProxyType
from typing import Any, get_args

import pytest

from tests.conftest import FakeBrowserProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import BROWSER_ENV_BY_PROVIDER, BROWSER_TIMEOUT
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
)
from web_router.providers._http import HttpBrowserProvider
from web_router.providers._utils import build_browser_payload
from web_router.providers.auto import AutoBrowserProvider
from web_router.providers.base import AsyncBrowserHandle, BrowserHandle, BrowserProvider
from web_router.registry import (
    available_browser_providers,
    get_browser_provider_class,
    register_browser_provider,
    unregister_browser_provider,
)
from web_router.types import (
    BROWSER_ALWAYS_SENT,
    BROWSER_PARAMETERS,
    REQUEST_VALUE,
    BrowserField,
    BrowserFieldRow,
    BrowserRequest,
    BrowserSession,
    BrowserState,
    StealthLevel,
)

ROUTER_ONLY_ARGUMENTS = {"self", "provider"}

PUBLIC_METHODS = ("open", "aopen", "release", "arelease", "status", "astatus", "validate")
"""Everything a caller reaches, all of which enforce something on the backend's behalf."""


def _fake(**overrides: Any) -> Any:
    """A fake browser backend with one behaviour swapped out."""
    return type("Swapped", (FakeBrowserProvider,), overrides)()


class _MinimalHttp(HttpBrowserProvider):
    """The smallest HTTP browser backend, so its base is held to the contract too."""

    name = "minimal-http"
    base_url = "https://browser.example.test"
    open_path = "/sessions"
    release_path = "/sessions/{session_id}/release"

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return {}

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        return BrowserSession(provider=self.name, session_id=payload["id"], cdp_url=payload["cdp"])


def _every_browser_provider() -> list[type[BrowserProvider]]:
    builtin = [get_browser_provider_class(name) for name in available_browser_providers()]
    return [*builtin, FakeBrowserProvider, _MinimalHttp]


# -- The shared surface ------------------------------------------------------


@pytest.mark.parametrize("method", [WebRouter.open_browser, AsyncWebRouter.open_browser], ids=["sync", "async"])
def test_router_open_browser_exposes_every_shared_option(method: object) -> None:
    """The two signatures restate BrowserRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == BROWSER_PARAMETERS | BROWSER_ALWAYS_SENT


def test_browser_parameters_tracks_the_model() -> None:
    assert set(BrowserRequest.model_fields) - BROWSER_ALWAYS_SENT == BROWSER_PARAMETERS


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    """configured_browser_providers() reads this table, so a gap there hides a provider."""
    assert set(BROWSER_ENV_BY_PROVIDER) == set(available_browser_providers())


@pytest.mark.parametrize("provider_cls", _every_browser_provider(), ids=lambda cls: cls.__name__)
def test_public_browser_methods_are_always_the_base_implementation(provider_cls: type[BrowserProvider]) -> None:
    """Validation, error wrapping and idempotent release live on the base class.

    A provider that overrode any of these -- directly or through an
    intermediate base like `HttpBrowserProvider` -- would bypass them.
    """
    for method in PUBLIC_METHODS:
        assert getattr(provider_cls, method) is getattr(BrowserProvider, method), (
            f"{provider_cls.__name__} overrides {method}() and would skip what the base class enforces"
        )


@pytest.mark.parametrize("provider_cls", _every_browser_provider(), ids=lambda cls: cls.__name__)
def test_providers_declare_only_real_options(provider_cls: type[BrowserProvider]) -> None:
    assert provider_cls.supported_parameters <= BROWSER_PARAMETERS
    assert not inspect.isabstract(provider_cls)


@pytest.mark.parametrize("provider_cls", _every_browser_provider(), ids=lambda cls: cls.__name__)
def test_stealth_levels_are_declared_exactly_when_stealth_is(provider_cls: type[BrowserProvider]) -> None:
    """Declared without levels, every level would be refused; levels without the option, none would be reached."""
    assert provider_cls.stealth_levels <= set(get_args(StealthLevel))
    assert bool(provider_cls.stealth_levels) == ("stealth" in provider_cls.supported_parameters)


@pytest.mark.parametrize("provider_cls", _every_browser_provider(), ids=lambda cls: cls.__name__)
def test_a_country_list_is_upper_case_codes_for_a_backend_that_maps_proxy_country(
    provider_cls: type[BrowserProvider],
) -> None:
    countries = provider_cls.proxy_countries
    if countries is None:
        return
    assert "proxy_country" in provider_cls.supported_parameters
    assert countries, "an empty list refuses every country; leave it None to send any"
    assert all(len(code) == 2 and code.isupper() for code in countries)


@pytest.mark.parametrize("provider_cls", _every_browser_provider(), ids=lambda cls: cls.__name__)
def test_every_co_requirement_names_options_the_backend_maps(provider_cls: type[BrowserProvider]) -> None:
    """A partner outside `supported_parameters` could never be set, so the option it guards could never be served."""
    for option, partners_by_value in provider_cls.co_requirements.items():
        assert option in provider_cls.supported_parameters
        for partners in partners_by_value.values():
            assert partners <= provider_cls.supported_parameters


def _http_browser_providers() -> list[type[HttpBrowserProvider]]:
    classes = [get_browser_provider_class(name) for name in available_browser_providers()]
    return [cls for cls in classes if issubclass(cls, HttpBrowserProvider)]


@pytest.mark.parametrize("provider_cls", _http_browser_providers(), ids=lambda cls: cls.__name__)
def test_the_stealth_table_lists_exactly_the_declared_levels(provider_cls: type[HttpBrowserProvider]) -> None:
    """Read through the body each level builds, since the table is the provider's own.

    A declared level the table does not list would be accepted and then fail to
    build; one the table lists but the class does not declare would be refused
    though it could be sent; and a declared level that sends nothing would be
    dropped -- the one outcome the level table exists to prevent.
    """
    provider = provider_cls(api_key="test-key")
    unset = provider._build_payload(BrowserRequest())
    for level in get_args(StealthLevel):
        if level in provider_cls.stealth_levels:
            assert provider._build_payload(BrowserRequest(stealth=level)) != unset, f"stealth={level!r} sends nothing"
        elif "stealth" in provider_cls.supported_parameters:
            with pytest.raises(ConfigurationError, match="lists no"):
                provider._build_payload(BrowserRequest(stealth=level))
        else:
            assert provider._build_payload(BrowserRequest(stealth=level)) == unset, "a stealth row no call can reach"


def test_local_declares_none_of_the_three() -> None:
    local = get_browser_provider_class("local")
    assert {"stealth", "proxy_country", "solve_captchas"}.isdisjoint(local.supported_parameters)
    assert local.stealth_levels == frozenset()


# -- Every session is released on every path ---------------------------------


def test_leaving_the_block_releases_the_session() -> None:
    provider = _fake()

    with provider.open(BrowserRequest()) as browser:
        assert provider.released == []

    assert provider.released == [browser.session_id]
    assert browser.released


async def test_leaving_the_async_block_releases_the_session() -> None:
    provider = _fake()

    async with await provider.aopen(BrowserRequest()) as browser:
        assert provider.released == []

    assert provider.released == [browser.session_id]


def test_an_exception_inside_the_block_still_releases_the_session() -> None:
    provider = _fake()

    with pytest.raises(RuntimeError, match="the caller's own bug"), provider.open(BrowserRequest()) as browser:
        raise RuntimeError("the caller's own bug")

    assert provider.released == [browser.session_id]


async def test_an_exception_inside_the_async_block_still_releases_the_session() -> None:
    provider = _fake()

    with pytest.raises(RuntimeError, match="the caller's own bug"):
        async with await provider.aopen(BrowserRequest()) as browser:
            raise RuntimeError("the caller's own bug")

    assert provider.released == [browser.session_id]


def test_closing_the_router_releases_every_session_still_open(fake_browser_provider: type) -> None:
    """The safety net for a handle the caller opened and lost track of."""
    router = WebRouter("fake")
    kept = router.open_browser()
    router.open_browser()
    kept.release()
    provider = router.get_browser_provider()

    router.close()

    assert provider.released == ["session-1", "session-2"], "each session released once, the lost one included"
    assert provider.closed, "the provider is closed after its sessions, not instead of them"


async def test_closing_the_async_router_releases_every_session_still_open(fake_browser_provider: type) -> None:
    router = AsyncWebRouter("fake")
    kept = await router.open_browser()
    await router.open_browser()
    await kept.release()
    provider = router.get_browser_provider()

    await router.aclose()

    assert provider.released == ["session-1", "session-2"]
    assert provider.closed


def test_sessions_are_released_before_the_providers_are_closed() -> None:
    """A release needs the provider's client, so closing it first would strand the session."""
    order: list[str] = []

    class _Ordered(FakeBrowserProvider):
        def _release(self, session_id: str) -> None:
            order.append("release")
            super()._release(session_id)

        def close(self) -> None:
            order.append("close")

    register_browser_provider("ordered", _Ordered)
    try:
        with WebRouter("ordered") as router:
            router.open_browser()
    finally:
        unregister_browser_provider("ordered")

    assert order == ["release", "close"]


def test_one_failed_release_does_not_stop_the_rest(fake_browser_provider: type) -> None:
    def _release(self: Any, session_id: str) -> None:
        raise RuntimeError("upstream 500")

    router = WebRouter("fake")
    stranded = router.open_browser(provider=_fake(_release=_release))
    router.open_browser()
    provider = router.get_browser_provider()

    with pytest.raises(ProviderError, match="upstream 500"):
        router.close()

    assert provider.released == ["session-1"], "the healthy session was still released"
    assert provider.closed
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del stranded, router
        gc.collect()


def test_a_release_that_failed_is_retried_by_the_next_close(fake_browser_provider: type) -> None:
    attempts: list[str] = []

    def _flaky(self: Any, session_id: str) -> None:
        attempts.append(session_id)
        if len(attempts) == 1:
            raise RuntimeError("upstream 500")
        self.released.append(session_id)

    router = WebRouter("fake")
    flaky = _fake(_release=_flaky)
    stranded = router.open_browser(provider=flaky)
    router.open_browser()

    with pytest.raises(ProviderError, match="upstream 500"):
        router.close()
    router.close()
    router.close()

    assert stranded.released
    assert attempts == [stranded.session_id, stranded.session_id], "retried once, then left alone"
    assert router.get_browser_provider().released == ["session-1"]


async def test_a_release_that_failed_is_retried_by_the_next_async_close(fake_browser_provider: type) -> None:
    attempts: list[str] = []

    def _flaky(self: Any, session_id: str) -> None:
        attempts.append(session_id)
        if len(attempts) == 1:
            raise RuntimeError("upstream 500")

    router = AsyncWebRouter("fake")
    stranded = await router.open_browser(provider=_fake(_release=_flaky))
    await router.open_browser()

    with pytest.raises(ProviderError, match="upstream 500"):
        await router.aclose()
    await router.aclose()

    assert stranded.released
    assert attempts == [stranded.session_id, stranded.session_id]


def test_releasing_twice_is_a_no_op() -> None:
    provider = _fake()
    browser = provider.open(BrowserRequest())

    browser.release()
    browser.release()

    assert provider.released == [browser.session_id], "the backend was asked once"


async def test_releasing_twice_is_a_no_op_asynchronously() -> None:
    provider = _fake()
    browser = await provider.aopen(BrowserRequest())

    await browser.release()
    await browser.release()

    assert provider.released == [browser.session_id]


def test_releasing_a_session_the_backend_already_ended_is_not_an_error() -> None:
    """What the caller wanted -- the session over -- is already true."""
    provider = _fake()
    browser = provider.open(BrowserRequest())
    provider.already_over.add(browser.session_id)

    browser.release()

    assert browser.released


async def test_releasing_an_expired_session_asynchronously_is_not_an_error() -> None:
    provider = _fake()
    browser = await provider.aopen(BrowserRequest())
    provider.already_over.add(browser.session_id)

    await browser.release()

    assert browser.released


def test_a_release_that_fails_on_a_live_session_raises() -> None:
    """Swallowing it would leave a billed session running with nothing said."""

    def _release(self: Any, session_id: str) -> None:
        raise RuntimeError("upstream 500")

    provider = _fake(_release=_release)
    browser = provider.open(BrowserRequest())

    with pytest.raises(ProviderError, match="upstream 500") as caught:
        browser.release()

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert not browser.released, "a session that is still running must not be reported as released"
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del caught, browser  # the traceback holds the handle too
        gc.collect()


def _refused_release(self: Any, session_id: str) -> None:
    raise RuntimeError("stop went wrong")


async def _arefused_release(self: Any, session_id: str) -> None:
    raise RuntimeError("stop went wrong")


def test_a_failed_release_of_a_crashed_session_is_not_an_error() -> None:
    """By default ``failed`` is a session that crashed: it bills no more, so there is nothing left to release."""
    provider = _fake(_release=_refused_release)
    provider.reported = "BROKEN"
    browser = provider.open(BrowserRequest())

    browser.release()

    assert browser.released


async def test_a_failed_async_release_of_a_crashed_session_is_not_an_error() -> None:
    provider = _fake(_arelease=_arefused_release)
    provider.reported = "BROKEN"
    browser = await provider.aopen(BrowserRequest())

    await browser.release()

    assert browser.released


def test_a_backend_that_leaves_failed_out_of_its_ended_states_raises_a_failed_release() -> None:
    """For a backend whose ``failed`` includes a stop that went wrong, the session may still bill."""
    provider = _fake(_release=_refused_release, release_ended_states=frozenset({"released", "expired"}))
    provider.reported = "BROKEN"
    browser = provider.open(BrowserRequest())

    with pytest.raises(ProviderError, match="stop went wrong"):
        browser.release()

    assert not browser.released
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


async def test_the_async_release_honours_a_narrowed_set_too() -> None:
    provider = _fake(_arelease=_arefused_release, release_ended_states=frozenset({"released", "expired"}))
    provider.reported = "BROKEN"
    browser = await provider.aopen(BrowserRequest())

    with pytest.raises(ProviderError, match="stop went wrong"):
        await browser.release()

    assert not browser.released
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


def test_a_collected_handle_that_was_never_released_warns() -> None:
    """Warns rather than releasing: a finalizer is no place for a network call."""
    provider = _fake()
    browser = provider.open(BrowserRequest())

    with pytest.warns(ResourceWarning, match="'session-1' on provider 'fake'"):
        del browser
        gc.collect()

    assert provider.released == [], "the finalizer must not reach the backend"


def test_a_collected_handle_that_was_released_says_nothing(recwarn: pytest.WarningsRecorder) -> None:
    provider = _fake()
    with provider.open(BrowserRequest()):
        pass

    gc.collect()

    assert not [warning for warning in recwarn if issubclass(warning.category, ResourceWarning)]


# -- What BrowserProvider guarantees on every backend's behalf ---------------


def test_an_option_the_backend_does_not_map_is_rejected_before_a_session_opens() -> None:
    provider = _fake(supported_parameters=frozenset({"timeout"}))

    with pytest.raises(UnsupportedParameterError, match="stealth"):
        provider.open(BrowserRequest(stealth="low"))

    assert provider.requests == [], "the session must be refused before it is paid for, not after"


async def test_an_unmapped_option_is_rejected_on_the_async_path_too() -> None:
    provider = _fake(supported_parameters=frozenset({"timeout"}))

    with pytest.raises(UnsupportedParameterError, match="proxy_country"):
        await provider.aopen(BrowserRequest(proxy_country="US"))

    assert provider.requests == []


def test_off_is_an_answer_not_an_absence() -> None:
    """`stealth="off"` asks for something; a backend that cannot say either way must refuse it."""
    provider = _fake(supported_parameters=frozenset(), stealth_levels=frozenset())

    with pytest.raises(UnsupportedParameterError, match="stealth"):
        provider.open(BrowserRequest(stealth="off"))


def _stealthy(**overrides: Any) -> Any:
    """A fake that maps stealth and a proxy country, with two of the three levels unless told otherwise."""
    defaults = {
        "supported_parameters": frozenset({"stealth", "proxy_country"}),
        "stealth_levels": frozenset({"low", "high"}),
    }
    return _fake(**{**defaults, **overrides})


def test_a_level_the_backend_lacks_is_refused_by_name_before_a_session_opens() -> None:
    """Serving `low` to a caller who asked for `off` would be the substitution the level table exists to prevent."""
    provider = _stealthy()

    with pytest.raises(UnsupportedParameterError, match="does not support stealth='off'") as caught:
        provider.open(BrowserRequest(stealth="off"))

    assert "'high', 'low'" in str(caught.value)
    assert caught.value.parameters == ["stealth"]
    assert caught.value.values == {"stealth": "off"}
    assert provider.requests == []


async def test_a_level_the_backend_lacks_is_refused_on_the_async_path_too() -> None:
    provider = _stealthy()

    with pytest.raises(UnsupportedParameterError, match="stealth='off'"):
        await provider.aopen(BrowserRequest(stealth="off"))

    assert provider.requests == []


def test_a_level_the_backend_has_reaches_it_as_asked() -> None:
    provider = _stealthy()

    provider.open(BrowserRequest(stealth="high")).release()

    assert provider.requests[0].stealth == "high"


def test_a_country_outside_the_backends_list_is_refused_as_a_level_is_before_a_session_opens() -> None:
    """One error for every value a backend maps the option for but cannot serve, named or through `auto`."""
    provider = _stealthy(proxy_countries=frozenset({"US", "DE"}))

    with pytest.raises(UnsupportedParameterError, match="does not support proxy_country='FR'") as caught:
        provider.open(BrowserRequest(proxy_country="FR"))

    assert "no proxy there" in str(caught.value)
    assert (caught.value.parameters, caught.value.values) == (["proxy_country"], {"proxy_country": "FR"})
    assert provider.requests == []


def test_a_country_is_matched_whatever_its_case_and_sent_as_the_caller_wrote_it() -> None:
    """The case each API wants is its field table's business, not the caller's."""
    provider = _stealthy(proxy_countries=frozenset({"US"}))

    provider.open(BrowserRequest(proxy_country="us")).release()

    assert provider.requests[0].proxy_country == "us"


def test_a_backend_with_no_country_list_sends_any_code() -> None:
    provider = _stealthy()

    provider.open(BrowserRequest(proxy_country="ZZ")).release()

    assert provider.requests[0].proxy_country == "ZZ"


_NEEDS_A_PROXY = MappingProxyType(
    {"stealth": {"low": frozenset({"proxy_country"}), "high": frozenset({"proxy_country"})}}
)
"""Anchor's shape: extra stealth only beside a proxy."""


def test_an_option_served_only_beside_another_is_refused_without_it_before_a_session_opens() -> None:
    """Missing, not unsupported: the backend serves stealth, and setting the partner is the fix."""
    provider = _stealthy(co_requirements=_NEEDS_A_PROXY)

    with pytest.raises(MissingParameterError, match="beside stealth='low'") as caught:
        provider.open(BrowserRequest(stealth="low"))

    assert caught.value.parameters == ["proxy_country"]
    assert "leave stealth unset" in str(caught.value)
    assert provider.requests == []


async def test_an_unmet_co_requirement_is_refused_on_the_async_path_too() -> None:
    provider = _stealthy(co_requirements=_NEEDS_A_PROXY)

    with pytest.raises(MissingParameterError, match="proxy_country"):
        await provider.aopen(BrowserRequest(stealth="high"))

    assert provider.requests == []


def test_a_co_requirement_met_or_not_triggered_opens() -> None:
    provider = _stealthy(co_requirements=_NEEDS_A_PROXY, stealth_levels=frozenset({"off", "low", "high"}))

    provider.open(BrowserRequest(stealth="low", proxy_country="US")).release()
    provider.open(BrowserRequest(stealth="off")).release()
    provider.open(BrowserRequest(proxy_country="US")).release()

    assert [request.stealth for request in provider.requests] == ["low", "off", None]


def test_whatever_a_backend_raises_is_wrapped() -> None:
    def _open(self: Any, request: BrowserRequest) -> BrowserSession:
        raise RuntimeError("upstream exploded")

    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        _fake(_open=_open).open(BrowserRequest())

    assert isinstance(caught.value.original, RuntimeError)
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_whatever_a_backend_raises_asynchronously_is_wrapped_too() -> None:
    async def _aopen(self: Any, request: BrowserRequest) -> BrowserSession:
        raise RuntimeError("upstream exploded")

    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        await _fake(_aopen=_aopen).aopen(BrowserRequest())

    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_router_error_passes_through_unwrapped() -> None:
    def _open(self: Any, request: BrowserRequest) -> BrowserSession:
        raise UnsupportedParameterError("fake", ["stealth"])

    with pytest.raises(UnsupportedParameterError):
        _fake(_open=_open).open(BrowserRequest())


# -- The default lifetime ----------------------------------------------------


def test_an_unset_timeout_is_sent_as_the_default_to_a_backend_that_maps_one() -> None:
    """The second exception to "unset means unset": a forgotten session is billed until it ends."""
    provider = _fake(supported_parameters=frozenset({"timeout"}))

    provider.open(BrowserRequest()).release()

    assert provider.requests[0].timeout == BROWSER_TIMEOUT


def test_a_timeout_the_caller_set_is_sent_as_it_is() -> None:
    provider = _fake(supported_parameters=frozenset({"timeout"}))

    provider.open(BrowserRequest(timeout=30)).release()

    assert provider.requests[0].timeout == 30


def test_a_backend_with_no_lifetime_limit_is_sent_no_default_and_does_not_refuse() -> None:
    """An idle-only backend must still open a session the caller asked nothing special of."""
    provider = _fake(supported_parameters=frozenset({"idle_timeout"}))

    provider.open(BrowserRequest()).release()

    assert provider.requests[0].timeout is None


def test_a_timeout_the_caller_set_is_still_refused_by_a_backend_that_cannot_honour_it() -> None:
    provider = _fake(supported_parameters=frozenset({"idle_timeout"}))

    with pytest.raises(UnsupportedParameterError, match="timeout"):
        provider.open(BrowserRequest(timeout=30))


def test_the_router_applies_the_same_default(fake_browser_provider: type) -> None:
    with WebRouter("fake") as router:
        router.open_browser()
        provider = router.get_browser_provider()

    assert provider.requests[0].timeout == BROWSER_TIMEOUT


# -- A CDP URL is a credential -----------------------------------------------


def test_the_cdp_url_never_appears_in_a_repr(fake_browser_provider: type) -> None:
    with WebRouter("fake") as router:
        browser = router.open_browser()
        secret = browser.cdp_url

        assert "SEKRIT" in secret, "explicit attribute access returns the plain URL"
        for shown in (repr(browser), str(browser), repr(browser.session), str(browser.session), repr(router)):
            assert "SEKRIT" not in shown
        assert "SEKRIT" not in browser.session.model_dump_json()
        assert "SEKRIT" not in str(browser.session.model_dump())
        assert "SEKRIT" not in str(browser.session.model_dump(mode="json"))
        assert browser.session.raw is not None, "raw stays readable as an attribute"
        assert "SEKRIT" in str(browser.session.raw)
        assert repr(browser) == f"<BrowserHandle provider='fake' session_id={browser.session_id!r}>"


async def test_the_async_handle_keeps_it_out_of_its_repr_too(fake_browser_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        browser = await router.open_browser()

        assert "SEKRIT" in browser.cdp_url
        assert "SEKRIT" not in repr(browser)
        assert isinstance(browser, AsyncBrowserHandle)


def test_the_sync_and_async_handles_are_different_classes() -> None:
    """Playwright's sync API refuses to run inside an event loop; a handle is for one world."""
    assert not issubclass(AsyncBrowserHandle, BrowserHandle)
    assert not issubclass(BrowserHandle, AsyncBrowserHandle)


# -- Session state -----------------------------------------------------------


def test_state_of_translates_a_status_through_the_backends_own_table() -> None:
    assert _fake().state_of("RUNNING", {"RUNNING": "running"}) == "running"


def test_state_of_raises_on_a_status_it_does_not_know() -> None:
    """A backend that changed under us must not be read as "still running" and kept paying for."""
    with pytest.raises(ProviderError, match="unrecognized session state"):
        _fake().state_of("astonished", {"RUNNING": "running"})


def test_an_unknown_status_reaches_the_caller_as_an_error() -> None:
    provider = _fake()
    with provider.open(BrowserRequest()) as browser:
        provider.reported = "astonished"

        with pytest.raises(ProviderError, match="astonished"):
            browser.status()

        provider.reported = "RUNNING"


async def test_the_handle_reports_state_and_stops_asking_once_released() -> None:
    provider = _fake()
    browser = await provider.aopen(BrowserRequest())

    assert await browser.status() == "running"
    await browser.release()

    provider.reported = "astonished"
    state: BrowserState = await browser.status()
    assert state == "released", "a session this handle ended is not asked about again"


def test_a_backend_that_cannot_report_state_says_so() -> None:
    provider = _fake(_fetch=BrowserProvider._fetch)
    with provider.open(BrowserRequest()) as browser, pytest.raises(ProviderError, match="cannot report"):
        browser.status()


def test_a_backend_that_cannot_report_state_still_raises_a_failed_release() -> None:
    """With no way to ask, a failed release cannot be assumed to have been an expired session."""

    def _release(self: Any, session_id: str) -> None:
        raise RuntimeError("upstream 500")

    provider = _fake(_fetch=BrowserProvider._fetch, _release=_release)
    browser = provider.open(BrowserRequest())

    with pytest.raises(ProviderError, match="upstream 500"):
        browser.release()
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


# -- Review follow-ups -------------------------------------------------------


async def test_the_async_path_applies_the_same_default_timeout() -> None:
    provider = _fake(supported_parameters=frozenset({"timeout"}))

    await (await provider.aopen(BrowserRequest())).release()

    assert provider.requests[0].timeout == BROWSER_TIMEOUT


async def test_an_unknown_status_reaches_the_async_caller_as_an_error() -> None:
    provider = _fake()
    async with await provider.aopen(BrowserRequest()) as browser:
        provider.reported = "astonished"

        with pytest.raises(ProviderError, match="astonished"):
            await browser.status()

        provider.reported = "RUNNING"


def _failing_release(self: Any, session_id: str) -> None:
    raise RuntimeError("upstream 500")


def test_a_failed_release_does_not_hide_the_blocks_own_exception() -> None:
    """The caller's bug is what they need to see; the stuck session is warned about beside it."""
    provider = _fake(_release=_failing_release)

    with (
        pytest.warns(ResourceWarning, match="could not be released.*upstream 500"),
        pytest.raises(ValueError, match="the caller's own bug"),
        provider.open(BrowserRequest()) as browser,
    ):
        raise ValueError("the caller's own bug")

    assert not browser.released
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


async def test_a_failed_async_release_does_not_hide_the_blocks_own_exception() -> None:
    async def _arelease(self: Any, session_id: str) -> None:
        raise RuntimeError("upstream 500")

    provider = _fake(_arelease=_arelease, _release=_failing_release)

    with pytest.warns(ResourceWarning, match="could not be released"), pytest.raises(ValueError):
        async with await provider.aopen(BrowserRequest()) as browser:
            raise ValueError("the caller's own bug")

    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


def test_a_failed_release_on_a_clean_exit_still_raises() -> None:
    """Nothing to mask, so the failure is the error."""
    provider = _fake(_release=_failing_release)
    browser = provider.open(BrowserRequest())

    with pytest.raises(ProviderError, match="upstream 500"), browser:
        pass

    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


def test_concurrent_opens_are_all_released_by_close(fake_browser_provider: type) -> None:
    """Tracking is locked, so no handle opened from another thread escapes the sweep."""
    router = WebRouter("fake")
    provider = router.get_browser_provider()
    threads = [threading.Thread(target=router.open_browser) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    router.close()

    assert len(provider.released) == 50


# -- The shared payload builder ----------------------------------------------

NESTED = {"timeout": ("session", "timeout", "max"), "idle_timeout": ("session", "timeout", "idle")}


def test_mapped_options_land_at_their_paths_and_unset_ones_are_not_sent() -> None:
    payload = build_browser_payload(BrowserRequest(timeout=90), NESTED, int)
    assert payload == {"session": {"timeout": {"max": 90}}}


def test_provider_options_merge_beneath_a_nested_mapped_option_at_every_depth() -> None:
    options = {"session": {"recording": False, "timeout": {"idle": 5}}, "browser": {"headless": True}}
    payload = build_browser_payload(BrowserRequest(timeout=90, provider_options=options), NESTED, int)

    assert payload == {
        "session": {"recording": False, "timeout": {"idle": 5, "max": 90}},
        "browser": {"headless": True},
    }
    assert options == {"session": {"recording": False, "timeout": {"idle": 5}}, "browser": {"headless": True}}


def test_a_none_or_any_mapping_on_the_way_to_a_mapped_field_is_merged_into() -> None:
    for branch in (None, MappingProxyType({"recording": False})):
        request = BrowserRequest(timeout=90, provider_options={"session": branch})
        expected = {"timeout": {"max": 90}} if branch is None else {"recording": False, "timeout": {"max": 90}}
        assert build_browser_payload(request, NESTED, int) == {"session": expected}


@pytest.mark.parametrize(
    ("fields", "options"),
    [
        ({"timeout": ("timeout",)}, {"timeout": 5}),
        (NESTED, {"session": {"timeout": {"max": 5}}}),
        (NESTED, {"session": {"timeout": 5}}),
    ],
    ids=["top-level", "nested", "occupied-branch"],
)
def test_provider_options_setting_a_mapped_field_raises_rather_than_one_side_winning(
    fields: dict[str, tuple[str, ...]], options: dict[str, Any]
) -> None:
    """The lifetime may be the router's default, which the caller never saw -- neither side may silently win."""
    with pytest.raises(ConfigurationError, match="set it in one place"):
        build_browser_payload(BrowserRequest(timeout=90, provider_options=options), fields, int)


# One table in each row shape a vendor needs: a path, a level lookup, and fields
# set together -- one of them a list, as Browserbase's `proxies` is.
ROWS: dict[str, BrowserFieldRow] = {
    "timeout": ("timeoutMinutes",),
    "solve_captchas": ("browser", "captcha_solver", "active"),
    "stealth": {
        "off": {("useStealth",): False, ("useUltraStealth",): False},
        "low": {("useStealth",): True},
        "high": {("useUltraStealth",): True},
    },
    "proxy_country": BrowserField(
        {("useProxy",): True, ("proxies",): [{"type": "vendor", "geolocation": {"country": REQUEST_VALUE}}]},
        case="upper",
    ),
}


def _minutes(seconds: float) -> int:
    return int(seconds // 60)


def test_a_lookup_row_sets_every_field_the_value_names_and_nothing_else() -> None:
    payload = build_browser_payload(BrowserRequest(stealth="off"), ROWS, _minutes)
    assert payload == {"useStealth": False, "useUltraStealth": False}

    payload = build_browser_payload(BrowserRequest(stealth="high"), ROWS, _minutes)
    assert payload == {"useUltraStealth": True}


def test_one_option_can_set_several_fields_with_its_value_cased_wherever_it_goes() -> None:
    payload = build_browser_payload(BrowserRequest(proxy_country="de"), ROWS, _minutes)

    assert payload == {"useProxy": True, "proxies": [{"type": "vendor", "geolocation": {"country": "DE"}}]}


def test_a_lower_case_row_sends_the_code_in_lower_case() -> None:
    fields = {"proxy_country": BrowserField({("session", "proxy", "country_code"): REQUEST_VALUE}, case="lower")}

    assert build_browser_payload(BrowserRequest(proxy_country="US"), fields, _minutes) == {
        "session": {"proxy": {"country_code": "us"}}
    }


def test_only_durations_go_through_the_unit_converter() -> None:
    """A flag at a bare path is sent as it is; converting it as seconds would send `0` for `True`."""
    request = BrowserRequest(timeout=600, solve_captchas=True)

    assert build_browser_payload(request, ROWS, _minutes) == {
        "timeoutMinutes": 10,
        "browser": {"captcha_solver": {"active": True}},
    }


def test_the_table_is_never_shared_into_a_payload() -> None:
    """The list a row produces is built anew, so a provider editing one body cannot change the next."""
    first = build_browser_payload(BrowserRequest(proxy_country="us"), ROWS, _minutes)
    first["proxies"][0]["geolocation"]["country"] = "XX"

    second = build_browser_payload(BrowserRequest(proxy_country="us"), ROWS, _minutes)
    assert second["proxies"][0]["geolocation"]["country"] == "US"


def test_provider_options_merge_beside_every_field_a_row_produces() -> None:
    options = {"browser": {"captcha_solver": {"provider": "x"}, "headless": False}}
    request = BrowserRequest(stealth="low", solve_captchas=False, provider_options=options)

    assert build_browser_payload(request, ROWS, _minutes) == {
        "useStealth": True,
        "browser": {"captcha_solver": {"provider": "x", "active": False}, "headless": False},
    }
    assert options == {"browser": {"captcha_solver": {"provider": "x"}, "headless": False}}


@pytest.mark.parametrize(
    ("request_fields", "options"),
    [
        ({"stealth": "off"}, {"useUltraStealth": True}),
        ({"proxy_country": "US"}, {"useProxy": False}),
        ({"proxy_country": "US"}, {"proxies": [{"type": "custom"}]}),
        ({"solve_captchas": True}, {"browser": {"captcha_solver": {"active": False}}}),
    ],
    ids=["lookup", "multi-field", "list-leaf", "nested-path"],
)
def test_provider_options_setting_any_produced_field_raises(
    request_fields: dict[str, Any], options: dict[str, Any]
) -> None:
    """A list is a leaf: one in provider_options is neither merged into nor appended to the produced one."""
    with pytest.raises(ConfigurationError, match="set it in one place"):
        build_browser_payload(BrowserRequest(**request_fields, provider_options=options), ROWS, _minutes)


@pytest.mark.parametrize(
    "fields",
    [
        {"solve_captchas": ("captchas",), "stealth": {"low": {("captchas",): True}}},
        {"solve_captchas": ("browser",), "stealth": {"low": {("browser", "stealth"): True}}},
        {"stealth": {"low": {("browser", "stealth"): True}}, "solve_captchas": ("browser",)},
        {"proxy_country": BrowserField({("proxy",): {"on": True}, ("proxy", "country"): REQUEST_VALUE})},
    ],
    ids=["same-path", "nested-under-a-placed-value", "placed-over-a-nested-one", "within-one-row"],
)
def test_two_fields_the_table_produces_that_meet_are_its_fault_not_the_callers(
    fields: dict[str, BrowserFieldRow],
) -> None:
    request = BrowserRequest(stealth="low", solve_captchas=True, proxy_country="US")

    with pytest.raises(ConfigurationError, match="field table sends") as caught:
        build_browser_payload(request, fields, _minutes)

    assert "provider_options" not in str(caught.value)


def test_fields_beside_each_other_in_one_object_do_not_meet() -> None:
    fields: dict[str, BrowserFieldRow] = {
        "solve_captchas": ("browser", "captchas"),
        "stealth": {"low": {("browser", "stealth"): True}},
    }

    payload = build_browser_payload(BrowserRequest(stealth="low", solve_captchas=True), fields, _minutes)

    assert payload == {"browser": {"captchas": True, "stealth": True}}


def test_a_case_on_a_value_that_is_not_text_is_a_table_bug() -> None:
    fields = {"solve_captchas": BrowserField({("captchas",): REQUEST_VALUE}, case="upper")}

    with pytest.raises(ConfigurationError, match="not text"):
        build_browser_payload(BrowserRequest(solve_captchas=True), fields, _minutes)


def test_the_request_value_is_filled_inside_a_tuple_too() -> None:
    fields = {"proxy_country": BrowserField({("proxies",): (REQUEST_VALUE, "fallback")})}

    assert build_browser_payload(BrowserRequest(proxy_country="US"), fields, _minutes) == {
        "proxies": ("US", "fallback")
    }


def test_a_clash_message_mentions_the_default_only_where_there_is_one() -> None:
    with pytest.raises(ConfigurationError, match="BROWSER_TIMEOUT when you pass none"):
        build_browser_payload(BrowserRequest(timeout=90, provider_options={"timeoutMinutes": 1}), ROWS, _minutes)

    with pytest.raises(ConfigurationError, match="set it in one place") as caught:
        build_browser_payload(BrowserRequest(stealth="low", provider_options={"useStealth": False}), ROWS, _minutes)
    assert "BROWSER_TIMEOUT" not in str(caught.value)
    assert "seconds" not in str(caught.value)


def test_a_lookup_that_does_not_list_the_value_sends_nothing() -> None:
    """Only a provider whose levels outrun its own table reaches this, and it must not open a session regardless."""
    fields: dict[str, BrowserFieldRow] = {"stealth": {"low": {("useStealth",): True}}}

    with pytest.raises(ConfigurationError, match="lists no stealth='high'"):
        build_browser_payload(BrowserRequest(stealth="high"), fields, _minutes)


# -- auto: a session that is opened is paid for ------------------------------


@pytest.mark.parametrize("provider_cls", _every_browser_provider(), ids=lambda cls: cls.__name__)
def test_only_auto_hands_back_a_handle_it_did_not_build(provider_cls: type[BrowserProvider]) -> None:
    """`_opened` is how `auto` returns its member's own handle; any other provider implements `_open`."""
    if provider_cls is AutoBrowserProvider:
        return
    for method in ("_opened", "_aopened"):
        assert getattr(provider_cls, method) is getattr(BrowserProvider, method), (
            f"{provider_cls.__name__} overrides {method}() and would skip the default lifetime and error wrapping"
        )


class _Stealthy(FakeBrowserProvider):
    name = "stealthy"
    supported_parameters = frozenset({"stealth"})
    stealth_levels = frozenset({"low", "high"})


class _ProxiedStealth(FakeBrowserProvider):
    """Every level, but stealth only beside a proxy -- Anchor's shape."""

    name = "proxied-stealth"
    supported_parameters = frozenset({"stealth", "proxy_country"})
    co_requirements = _NEEDS_A_PROXY


class _UsOnly(FakeBrowserProvider):
    """A proxy in one country only."""

    name = "us-only"
    proxy_countries = frozenset({"US"})


class _OpensThenBreaks(FakeBrowserProvider):
    """Opens a session, then fails the first thing asked of it afterwards."""

    name = "opens-then-breaks"

    def _fetch(self, session_id: str) -> BrowserState:
        raise ProviderError(self.name, "status endpoint is down")


@pytest.fixture
def auto_members() -> Any:
    register_browser_provider(_Stealthy.name, _Stealthy)
    register_browser_provider(_ProxiedStealth.name, _ProxiedStealth)
    register_browser_provider(_UsOnly.name, _UsOnly)
    register_browser_provider(_OpensThenBreaks.name, _OpensThenBreaks)
    register_browser_provider("fake", FakeBrowserProvider)
    yield
    for name in (_Stealthy.name, _ProxiedStealth.name, _UsOnly.name, _OpensThenBreaks.name, "fake"):
        unregister_browser_provider(name)


@pytest.mark.usefixtures("auto_members")
def test_auto_with_stealth_skips_local_without_launching_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """`local` has no stealth, so `auto` must not hand back a detectable browser -- nor even start one."""
    local = get_browser_provider_class("local")
    launched: list[BrowserRequest] = []
    monkeypatch.setattr(local, "_open", lambda self, request: launched.append(request))

    auto = AutoBrowserProvider(chain=(("local", {}), (_Stealthy.name, {})))
    with auto.open(BrowserRequest(stealth="low")) as browser:
        assert browser.provider == _Stealthy.name
    assert launched == []

    with pytest.raises(UnsupportedParameterError, match="local does not map 'stealth'"):
        AutoBrowserProvider(chain=(("local", {}),)).open(BrowserRequest(stealth="high"))
    assert launched == []


@pytest.mark.usefixtures("auto_members")
def test_auto_passes_over_a_member_without_the_level_for_one_that_has_it() -> None:
    auto = AutoBrowserProvider(chain=((_Stealthy.name, {}), ("fake", {})))

    with auto.open(BrowserRequest(stealth="off")) as browser:
        assert browser.provider == "fake"

    assert 0 not in auto._members, "a member passed over on its class is never even built"


@pytest.mark.usefixtures("auto_members")
def test_auto_names_the_level_when_no_member_serves_it() -> None:
    with pytest.raises(UnsupportedParameterError) as caught:
        AutoBrowserProvider(chain=((_Stealthy.name, {}), ("local", {}))).open(BrowserRequest(stealth="off"))

    message = str(caught.value)
    assert "does not support stealth='off'" in message
    assert "stealthy does not support stealth='off'" in message
    assert "local does not map 'stealth'" in message
    assert (caught.value.parameters, caught.value.values) == (["stealth"], {"stealth": "off"})


@pytest.mark.usefixtures("auto_members")
def test_auto_passes_over_a_member_with_no_proxy_in_the_country_and_names_it() -> None:
    auto = AutoBrowserProvider(chain=((_UsOnly.name, {}), ("fake", {})))

    with auto.open(BrowserRequest(proxy_country="FR")) as browser:
        assert browser.provider == "fake"
    assert 0 not in auto._members

    with pytest.raises(UnsupportedParameterError) as caught:
        AutoBrowserProvider(chain=((_UsOnly.name, {}), ("local", {}))).open(BrowserRequest(proxy_country="fr"))

    message = str(caught.value)
    assert "us-only does not support proxy_country='fr'" in message
    assert "local does not map 'proxy_country'" in message
    assert (caught.value.parameters, caught.value.values) == (["proxy_country"], {"proxy_country": "fr"})


@pytest.mark.usefixtures("auto_members")
def test_auto_passes_over_a_member_whose_partner_option_is_unset() -> None:
    auto = AutoBrowserProvider(chain=((_ProxiedStealth.name, {}), ("fake", {})))

    with auto.open(BrowserRequest(stealth="low")) as browser:
        assert browser.provider == "fake"

    with auto.open(BrowserRequest(stealth="low", proxy_country="US")) as browser:
        assert browser.provider == _ProxiedStealth.name


@pytest.mark.usefixtures("auto_members")
def test_auto_asks_for_the_partner_when_it_is_all_that_stands_in_the_way() -> None:
    """As for browse: when a member would serve the request once something is set, setting it is the fix."""
    chain = ((_ProxiedStealth.name, {}), ("local", {}))

    with pytest.raises(MissingParameterError) as caught:
        AutoBrowserProvider(chain=chain).open(BrowserRequest(stealth="high"))

    message = str(caught.value)
    assert "proxied-stealth needs 'proxy_country' beside stealth='high'" in message
    assert "local does not map 'stealth'" in message
    assert caught.value.parameters == ["proxy_country"]


@pytest.mark.usefixtures("auto_members")
async def test_async_auto_routes_around_levels_and_partners_the_same_way() -> None:
    """A partner is needed only at the values that name it: `off` needs no proxy, so the member serves it."""
    auto = AutoBrowserProvider(chain=((_Stealthy.name, {}), (_ProxiedStealth.name, {}), ("fake", {})))

    async with await auto.aopen(BrowserRequest(stealth="off")) as browser:
        assert browser.provider == _ProxiedStealth.name

    with pytest.raises(MissingParameterError, match="proxy_country"):
        await AutoBrowserProvider(chain=((_ProxiedStealth.name, {}),)).aopen(BrowserRequest(stealth="low"))


@pytest.mark.usefixtures("auto_members")
def test_auto_never_opens_a_second_session_after_a_failure_once_one_is_open() -> None:
    auto = AutoBrowserProvider(chain=((_OpensThenBreaks.name, {}), ("fake", {})))

    with pytest.raises(ProviderError, match="status endpoint is down"), auto.open(BrowserRequest()) as browser:
        browser.status()

    first, second = auto._member(0), auto._member(1)
    assert isinstance(first, FakeBrowserProvider) and isinstance(second, FakeBrowserProvider)
    assert len(first.requests) == 1
    assert first.released == [browser.session_id], "the failure's session is released by the member that opened it"
    assert second.requests == [], "no second session may be opened to cover for the first"


@pytest.mark.usefixtures("auto_members")
async def test_async_auto_never_opens_a_second_session_either() -> None:
    auto = AutoBrowserProvider(chain=((_OpensThenBreaks.name, {}), ("fake", {})))

    with pytest.raises(ProviderError, match="status endpoint is down"):
        async with await auto.aopen(BrowserRequest()) as browser:
            await browser.status()

    first, second = auto._member(0), auto._member(1)
    assert isinstance(first, FakeBrowserProvider) and isinstance(second, FakeBrowserProvider)
    assert first.released == [browser.session_id]
    assert second.requests == []

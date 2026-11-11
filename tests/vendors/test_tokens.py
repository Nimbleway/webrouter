"""The OAuth token cache: how long a token is believed, and one exchange per token across sync and async."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tests.vendors.conftest import MakeCaller, Recorder, _call
from tests.vendors.data import CREDENTIALS, OAUTH
from web_router.exceptions import ProviderError
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.schema import VendorOAuth2
from web_router.vendors.tokens import TokenCache, token_request

_OAUTH = VendorOAuth2.model_validate(OAUTH["auth"]["oauth2"])


class _TokenEndpoint:
    """A token endpoint answering ``body``, counting its calls, optionally slow."""

    def __init__(self, body: bytes, delay: float = 0.0) -> None:
        self.body, self.delay, self.calls = body, delay, 0
        self._guard = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._guard:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return httpx.Response(200, content=self.body, headers={"content-type": "application/json"})


def _body(expires_in: str | None) -> bytes:
    lifetime = "" if expires_in is None else f', "expires_in": {expires_in}'
    return f'{{"access_token": "t"{lifetime}}}'.encode()


def _twice(endpoint: _TokenEndpoint) -> None:
    cache, request = TokenCache(), token_request("oauthco", _OAUTH, CREDENTIALS["oauthco"])
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        assert cache.get(request, client) == "t"
        assert cache.get(request, client) == "t"


@pytest.mark.parametrize("expires_in", ["0", "-5", "30"])
def test_a_lifetime_at_or_inside_the_refresh_margin_is_not_reused(expires_in: str) -> None:
    endpoint = _TokenEndpoint(_body(expires_in))
    _twice(endpoint)
    assert endpoint.calls == 2


@pytest.mark.parametrize("expires_in", ["3600", None])
def test_a_long_or_unstated_lifetime_is_reused(expires_in: str | None) -> None:
    endpoint = _TokenEndpoint(_body(expires_in))
    _twice(endpoint)
    assert endpoint.calls == 1


@pytest.mark.parametrize("expires_in", ["NaN", "Infinity"])
def test_a_non_finite_lifetime_is_an_unreadable_answer(expires_in: str) -> None:
    with pytest.raises(ProviderError, match="not finite"):
        _twice(_TokenEndpoint(_body(expires_in)))


def test_a_boolean_lifetime_is_an_unreadable_answer() -> None:
    with pytest.raises(ProviderError, match="bool"):
        _twice(_TokenEndpoint(_body("true")))


def test_a_sync_call_on_the_loop_thread_does_not_hang_behind_an_async_one() -> None:
    """An async call holds the token across its await; a sync call made on the same thread must still finish."""
    cache, request = TokenCache(), token_request("oauthco", _OAUTH, CREDENTIALS["oauthco"])
    got: list[str] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, content=_body("3600"))

    async def both() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as aclient:
            task = asyncio.create_task(cache.aget(request, aclient))
            await asyncio.sleep(0.05)
            with httpx.Client(transport=httpx.MockTransport(_TokenEndpoint(_body("3600")))) as client:
                got.append(cache.get(request, client))
            got.append(await task)

    runner = threading.Thread(target=lambda: asyncio.run(both()), daemon=True)
    runner.start()
    runner.join(timeout=2)
    assert not runner.is_alive(), "the sync call hung the event loop"
    assert got == ["t", "t"]


def test_a_sync_and_an_async_call_on_different_threads_both_finish() -> None:
    endpoint = _TokenEndpoint(_body("3600"), delay=0.2)
    cache, request = TokenCache(), token_request("oauthco", _OAUTH, CREDENTIALS["oauthco"])
    got: list[str] = []

    def sync_side() -> None:
        with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
            got.append(cache.get(request, client))

    async def async_side() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            got.append(await cache.aget(request, client))

    threads = [threading.Thread(target=sync_side), threading.Thread(target=sync_side)]
    for thread in threads:
        thread.start()
    asyncio.run(async_side())
    for thread in threads:
        thread.join()
    assert got == ["t", "t", "t"]
    assert endpoint.calls <= 2  # the two sync calls share one exchange; the async one may make its own


@pytest.mark.parametrize(
    "change",
    [
        {"token_url": "https://{region}.example.com/tok"},
        {"scope": "read:{tenant}"},
    ],
)
def test_an_oauth_token_url_or_scope_holds_no_placeholder(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="cannot hold a placeholder"):
        VendorOAuth2.model_validate({**OAUTH["auth"]["oauth2"], **change})


def test_a_literal_scope_is_sent_as_written() -> None:
    oauth = VendorOAuth2.model_validate({**OAUTH["auth"]["oauth2"], "scope": "read write"})
    assert token_request("oauthco", oauth, CREDENTIALS["oauthco"]).form["scope"] == "read write"


# -- token failures, through the caller ---------------------------------------


def _token_failure(respond: Any) -> Any:
    class Failing(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.host == "auth.oauth.test":
                return respond(request)
            return httpx.Response(200, json={})

    return Failing()


@pytest.mark.parametrize(
    "respond",
    [
        lambda request: httpx.Response(200, text="<html>not json</html>"),
        lambda request: httpx.Response(200, json=["a", "list"]),
        lambda request: httpx.Response(200, json={"access_token": "t", "expires_in": "soon"}),
    ],
    ids=["not-json", "not-an-object", "bad-expiry"],
)
def test_a_malformed_token_answer_is_a_provider_error(catalog: VendorCatalog, make_caller: MakeCaller, respond: Any):
    with pytest.raises(ProviderError, match="not a token"):
        _call(make_caller(_token_failure(respond)), catalog, "oauthco.report.query", {"customer_id": "1", "query": "q"})


def test_an_unreachable_token_endpoint_is_a_provider_error(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out sending refresh-secret-abc", request=request)

    with pytest.raises(ProviderError, match="could not reach the token endpoint") as raised:
        _call(make_caller(_token_failure(respond)), catalog, "oauthco.report.query", {"customer_id": "1", "query": "q"})
    assert "refresh-secret-abc" not in str(raised.value)


def test_a_token_being_minted_does_not_hold_up_another_account() -> None:
    oauth = _OAUTH
    first = token_request("oauthco", oauth, {**CREDENTIALS["oauthco"], "refresh_token": "account-one"})
    second = token_request("oauthco", oauth, {**CREDENTIALS["oauthco"], "refresh_token": "account-two"})
    cache = TokenCache()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "x"}))
    )
    held = cache._lock(first.key)
    held.acquire()  # the first account's exchange is in flight
    try:
        done = threading.Event()
        worker = threading.Thread(target=lambda: (cache.get(second, client), done.set()))
        worker.start()
        assert done.wait(timeout=2), "the second account waited on the first account's lock"
    finally:
        held.release()
        client.close()
